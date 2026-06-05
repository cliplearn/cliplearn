import os
import sys
import io
import time
import json
import re
import hashlib
import uuid
import socket
import urllib.request
import urllib.parse
import threading
import asyncio

from flask import request, jsonify, render_template, send_file

# 统一使用绝对导入（app.py 已将 server 目录加入 sys.path）
from database import (
    get_db_connection,
    get_user_status,
    save_user_status,
    count_active,
    get_active_records,
    insert_active,
    move_oldest_active_to_archive,
    delete_from_active_or_archive,
    periodic_cleanup,
    APP_ROOT, DATA_DIR, AUDIO_DIR,
    MAX_ACTIVE_FREE, MAX_ACTIVE,
)

# ═══════════════════════════════════════════
# 可选依赖：导入失败不影响基本功能
# ═══════════════════════════════════════════
try:
    import boto3
except Exception:
    boto3 = None

try:
    import edge_tts
except Exception:
    edge_tts = None

# ═══════════════════════════════════════════
# Tesseract-OCR 初始化
# ═══════════════════════════════════════════

def _find_tesseract():
    """Auto-detect Tesseract-OCR installation path on Windows."""
    import subprocess
    import winreg

    # 1) Try where tesseract (respects PATH)
    try:
        result = subprocess.run(
            ['where', 'tesseract'], capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0 and result.stdout.strip():
            path = result.stdout.strip().splitlines()[0].strip()
            if os.path.isfile(path):
                return path
    except Exception:
        pass

    # 2) Try Windows registry
    reg_keys = [
        (winreg.HKEY_LOCAL_MACHINE, r'SOFTWARE\Tesseract-OCR'),
        (winreg.HKEY_LOCAL_MACHINE, r'SOFTWARE\WOW6432Node\Tesseract-OCR'),
        (winreg.HKEY_CURRENT_USER,  r'SOFTWARE\Tesseract-OCR'),
    ]
    for hkey, subkey in reg_keys:
        try:
            with winreg.OpenKey(hkey, subkey) as key:
                install_dir, _ = winreg.QueryValueEx(key, 'InstallDir')
                candidate = os.path.join(install_dir, 'tesseract.exe')
                if os.path.isfile(candidate):
                    return candidate
        except OSError:
            pass

    # 3) Common install paths
    common = [
        os.path.expandvars(r'%ProgramFiles%\Tesseract-OCR\tesseract.exe'),
        os.path.expandvars(r'%ProgramFiles(x86)%\Tesseract-OCR\tesseract.exe'),
        os.path.expandvars(r'%LOCALAPPDATA%\Tesseract-OCR\tesseract.exe'),
        r'C:\Program Files\Tesseract-OCR\tesseract.exe',
    ]
    for candidate in common:
        if os.path.isfile(candidate):
            return candidate

    # 4) Last resort: trust PATH
    return 'tesseract'


# Windows: 必须在 import pytesseract 之前设置 TESSDATA_PREFIX
_tesseract_path = None
if os.name == 'nt':
    _tesseract_path = _find_tesseract()
    _tessdata_dir = os.path.join(DATA_DIR, 'tessdata')
    if os.path.isdir(_tessdata_dir):
        os.environ['TESSDATA_PREFIX'] = _tessdata_dir

import pytesseract
from PIL import Image

if os.name == 'nt' and _tesseract_path:
    pytesseract.pytesseract.tesseract_cmd = _tesseract_path

from deep_translator import GoogleTranslator
from send2trash import send2trash


# ═══════════════════════════════════════════
# AWS 服务检测
# ═══════════════════════════════════════════
textract_client = None
translate_client = None
polly_client = None
aws_available = False

try:
    sts = boto3.client('sts', region_name='us-east-1')
    sts.get_caller_identity()
    textract_client = boto3.client('textract', region_name='us-east-1')
    translate_client = boto3.client('translate', region_name='us-east-1')
    polly_client = boto3.client('polly', region_name='us-east-1')
    aws_available = True
    print("[OK] AWS 服务已就绪")
except Exception:
    pass

if boto3 is None:
    print("[信息] boto3 未安装，使用离线模式")


# ═══════════════════════════════════════════
# 文本处理工具函数
# ═══════════════════════════════════════════

def is_mostly_chinese(text):
    if not text:
        return False
    chinese_chars = [ch for ch in text if '一' <= ch <= '鿿']
    return len(chinese_chars) / len(text) > 0.3


def _clean_text(text):
    """数据清洗：去头（垃圾前缀）、去腰（断行/连字符）、去尾（垃圾后缀）"""
    import unicodedata

    if not text or not text.strip():
        return ''

    # ── 1. 基础归一化 ──
    cleaned = ''
    for ch in text:
        cat = unicodedata.category(ch)
        if cat == 'Cc' and ch not in ('\n', '\r', '\t'):
            cleaned += ' '
        else:
            cleaned += ch
    cleaned = cleaned.replace('　', ' ')          # 全角空格
    cleaned = re.sub(r'[ \t]+', ' ', cleaned)
    cleaned = re.sub(r'[ \t]*\n[ \t]*', '\n', cleaned)
    cleaned = cleaned.strip()
    if not cleaned:
        return ''

    # ── 1.5. 中文校正：合并 OCR 产生的字间空格 ──
    cleaned = re.sub(r'(?<=[一-鿿])\s+(?=[一-鿿])', '', cleaned)
    cleaned = re.sub(r'(?<=[一-鿿])\s+(?=[　-〿＀-￯])', '', cleaned)

    # ── 2. 去头：垃圾前缀 ──
    cleaned = re.sub(r'^[a-zA-Z0-9]\s*\n\s*', '', cleaned)
    cleaned = re.sub(r'^\d{1,4}\s*\n\s*(?=[A-Za-z])', '', cleaned, count=1)
    cleaned = re.sub(r'^[^\w\s]{1,2}\s*(?=[A-Za-z])', '', cleaned, count=1)
    cleaned = re.sub(r'^\s*[^\w\s]+\s*\n+', '', cleaned, count=1)
    cleaned = re.sub(r'^[^\w\s]\s(?=[A-Za-z])', '', cleaned)

    # ── 3. 去腰：修复 OCR 断行 ──
    cleaned = re.sub(r'(\w)-\s*\n\s*(\w)', r'\1\2', cleaned)
    cleaned = re.sub(r'([a-z])\s*\n\s*([a-z])', r'\1 \2', cleaned)
    cleaned = re.sub(r'([a-z])\s*\n\s*([A-Z])', r'\1\2', cleaned)
    cleaned = re.sub(r'([.?!][\"\')»]?\s*)\n+', r'\1\n\n', cleaned)
    cleaned = re.sub(r'(?<!\n)\n(?!\n)', ' ', cleaned)
    cleaned = re.sub(r'\n{3,}', '\n\n', cleaned)

    # ── 4. 去尾：垃圾后缀 ──
    cleaned = re.sub(r'\n\s*\d{1,4}\s*$', '', cleaned)
    cleaned = re.sub(r'\n\s*[A-Za-z0-9]\s*$', '', cleaned)
    cleaned = re.sub(r'\s+\d{1,4}\s*$', '', cleaned)
    cleaned = re.sub(r'\s+[^\w\s]{1,3}\s*$', '', cleaned)
    cleaned = re.sub(r'\s*[-_]\s*$', '', cleaned)

    # ── 5. 终清 ──
    cleaned = re.sub(r'[ \t]+', ' ', cleaned)
    cleaned = re.sub(r'[ \t]*\n[ \t]*', '\n', cleaned)
    cleaned = cleaned.strip()
    while cleaned.endswith('\n'):
        cleaned = cleaned[:-1]

    return cleaned.strip()


def _truncate_text(text, max_words=80):
    """截断文本，保持句子完整"""
    words = text.split()
    if len(words) <= max_words:
        return text
    truncated = ' '.join(words[:max_words])
    for punct in ('. ', '? ', '! ', '.\n', '?\n', '!\n'):
        idx = truncated.rfind(punct)
        if idx != -1:
            return truncated[:idx + 1].strip()
    return truncated.rsplit(' ', 1)[0]


def _add_pauses(text):
    """在标点后插入额外空格，让 TTS 自然停顿"""
    text = re.sub(r'<[^>]+>', '', text)
    text = re.sub(r'([.!?])\s+', r'\1  ', text)
    text = re.sub(r'([,;:])\s+', r'\1  ', text)
    text = text.replace('\n\n', '. ')
    text = text.replace('\n', ', ')
    return text


# ═══════════════════════════════════════════
# 翻译函数
# ═══════════════════════════════════════════

def _extract_with_aws(image_bytes):
    resp = textract_client.detect_document_text(Document={'Bytes': image_bytes})
    return " ".join([b['Text'] for b in resp['Blocks'] if b['BlockType'] == 'LINE']).strip()


def _extract_with_ocr_offline(image_bytes):
    """使用系统 Tesseract-OCR 作为离线回退方案"""
    try:
        img = Image.open(io.BytesIO(image_bytes))
        text = pytesseract.image_to_string(img, lang='chi_sim+eng').strip()
        return text if text else None
    except Exception:
        return None


def _translate_with_aws(text, source, target):
    trans = translate_client.translate_text(
        Text=text, SourceLanguageCode=source, TargetLanguageCode=target
    )
    return trans['TranslatedText'], trans.get('SourceLanguageCode', source)


def _translate_offline(text, source, target):
    """离线翻译: 仅支持 zh<->en，使用 deep-translator (Google Translate)"""
    if source == 'auto':
        source = 'zh' if is_mostly_chinese(text) else 'en'
    if source == target:
        return text, source
    if source == 'zh' and target == 'en':
        result = GoogleTranslator(source='zh-CN', target='en').translate(text)
    elif source == 'en' and target == 'zh':
        result = GoogleTranslator(source='en', target='zh-CN').translate(text)
    else:
        result = text
    return result, source


# ═══════════════════════════════════════════
# 语音合成
# ═══════════════════════════════════════════

def _speak_with_aws(text, output_path):
    polly_resp = polly_client.synthesize_speech(
        Engine='neural', OutputFormat='mp3', Text=text, VoiceId='Ruth'
    )
    with open(output_path, 'wb') as f:
        f.write(polly_resp['AudioStream'].read())


# Persistent event loop for Edge-TTS
_tts_loop = None
_tts_loop_ready = threading.Event()


def _init_tts_loop():
    global _tts_loop
    if os.name == 'nt':
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    _tts_loop = asyncio.new_event_loop()
    asyncio.set_event_loop(_tts_loop)
    _tts_loop_ready.set()
    _tts_loop.run_forever()


threading.Thread(target=_init_tts_loop, name='tts-loop', daemon=True).start()
_tts_loop_ready.wait()


def _tts_async(text, voice, output_path):
    """在独立事件循环中运行 edge_tts"""
    async def _gen():
        comm = edge_tts.Communicate(text, voice)
        await comm.save(output_path)

    future = asyncio.run_coroutine_threadsafe(_gen(), _tts_loop)
    future.result()


def _speak_offline(text, output_path):
    """使用 Edge-TTS 女声 (Jenny) 朗读英文"""
    paused_text = _add_pauses(text)
    try:
        _tts_async(paused_text, 'en-US-JennyNeural', output_path)
    except Exception as e:
        print("[TTS] 女声合成失败: {}".format(e))
        if not os.path.exists(output_path):
            open(output_path, 'wb').close()


def _speak_male_offline(text, output_path):
    """使用 Edge-TTS 男声 (Guy) 朗读英文"""
    paused_text = _add_pauses(text)
    try:
        _tts_async(paused_text, 'en-US-GuyNeural', output_path)
    except Exception as e:
        print("[TTS] 男声合成失败: {}".format(e))
        if not os.path.exists(output_path):
            open(output_path, 'wb').close()


# ═══════════════════════════════════════════
# 统计服务器相关
# ═══════════════════════════════════════════

CLIPLEARN_VERSION = "1.0.0"
CLIPLEARN_SERVER = os.environ.get("CLIPLEARN_SERVER", "https://stats.cliplearn.ai")


def _get_machine_id():
    """基于 MAC 地址生成唯一机器标识"""
    try:
        node = uuid.getnode()
        return hashlib.sha256("cliplearn:{}".format(node).encode()).hexdigest()[:32]
    except Exception:
        return hashlib.sha256(
            "cliplearn:{}:{}".format(socket.gethostname(), uuid.uuid4()).encode()
        ).hexdigest()[:32]


def _get_geo():
    """通过 ipapi.co 获取本机公网 IP 的大致地理位置"""
    try:
        req = urllib.request.Request(
            "https://ipapi.co/json/",
            headers={"User-Agent": "ClipLearn/1.0"}
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            return json.loads(resp.read().decode())
    except Exception:
        return {}


def send_heartbeat():
    """后台线程：获取地理位置后向统计服务器发送心跳"""
    try:
        machine_id = _get_machine_id()
        hostname = socket.gethostname()
        geo = _get_geo()

        payload = {
            "machine_id": machine_id,
            "hostname": hostname,
            "latitude": geo.get("latitude"),
            "longitude": geo.get("longitude"),
            "city": geo.get("city", ""),
            "region": geo.get("region", ""),
            "country": geo.get("country_code", ""),
            "version": CLIPLEARN_VERSION,
        }

        req = urllib.request.Request(
            "{}/api/heartbeat".format(CLIPLEARN_SERVER),
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json", "User-Agent": "ClipLearn/1.0"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            if resp.status == 200:
                city_str = geo.get("city", "") or "Unknown"
                print("[心跳] 已上报 -> {}".format(city_str))
    except Exception as e:
        print("[心跳] 上报失败（不影响使用）: {}".format(e))


# ═══════════════════════════════════════════
# 路由注册
# ═══════════════════════════════════════════

def register_routes(app):
    """将所有路由注册到 Flask 实例上"""

    # 缓存控制
    @app.after_request
    def no_cache(response):
        response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
        response.headers['Pragma'] = 'no-cache'
        response.headers['Expires'] = '0'
        return response

    # ── 1. 页面渲染 ──
    @app.route('/')
    def index():
        return render_template('index.html')

    @app.route('/api/status')
    def api_health():
        """健康检查 — Electron 主进程轮询此接口确认后端就绪"""
        return jsonify({"status": "ok"})

    # ── 2. 数据接口 ──
    @app.route('/api/records', methods=['GET'])
    def get_records():
        """获取所有活跃的学习记录"""
        try:
            records = get_active_records()
            return jsonify(records)
        except Exception as e:
            return jsonify({"status": "error", "message": str(e)}), 500

    @app.route('/api/records', methods=['POST'])
    def add_record():
        """新增一条学习记录"""
        data = request.json
        record_id = data.get('id', str(time.time()))
        english = data.get('english', '')
        chinese = data.get('chinese', '')
        audio_path = data.get('audio_path', '')
        created_at = data.get('created_at', time.time())

        try:
            insert_active({
                "id": record_id,
                "english": english,
                "chinese": chinese,
                "audio_path": audio_path,
                "created_at": created_at,
            })
            return jsonify({"status": "success", "id": record_id})
        except Exception as e:
            return jsonify({"status": "error", "message": str(e)}), 500

    # ── 3. 核心：截图 OCR → 翻译 → 语音合成 ──
    @app.route("/api/process", methods=['POST'])
    def process_clip():
        status = get_user_status()
        current_time = time.time()

        if status["token_remaining"] > 0 and status["buffer_start_time"] is not None:
            status["buffer_start_time"] = None
            save_user_status(status)

        if status["token_remaining"] <= 0:
            if status["buffer_start_time"] is None:
                status["buffer_start_time"] = current_time
                save_user_status(status)
            else:
                if (current_time - status["buffer_start_time"]) > 604800:
                    return jsonify({
                        "error": "TOKEN_EXCEEDED",
                        "message": "已为您成功提纯海量语言资产，一星期无感缓冲期已满，请补充能量。"
                    }), 402

        try:
            route = request.form.get('route', 'zh_en')
            try:
                native_lang, target_lang = route.split('_')
            except ValueError:
                native_lang, target_lang = 'zh', 'en'

            # ---- 文本提取 ----
            text_field = (request.form.get('text') or '').strip()
            image_bytes = None
            if 'image' in request.files:
                image_bytes = request.files['image'].read()

            if text_field:
                extracted_text = text_field
            elif image_bytes:
                if aws_available:
                    extracted_text = _extract_with_aws(image_bytes)
                else:
                    extracted_text = _extract_with_ocr_offline(image_bytes)
                    if not extracted_text:
                        return jsonify({"error": "未能识别出文字"}), 400
            else:
                return jsonify({"error": "请提供文本或配置 AWS 凭证以启用 OCR"}), 400

            if not extracted_text:
                return jsonify({"error": "未能识别出文字"}), 400

            # 数据清洗
            extracted_text = _clean_text(extracted_text)
            if not extracted_text:
                return jsonify({"error": "未能识别出文字"}), 400

            # 截断过长的文本
            extracted_text = _truncate_text(extracted_text, max_words=80)

            # ---- 翻译分流 ----
            if aws_available:
                trans_result, detected = _translate_with_aws(extracted_text, "auto", target_lang)
            else:
                trans_result, detected = _translate_offline(extracted_text, "auto", target_lang)

            if detected == target_lang:
                english_text = extracted_text
                if aws_available:
                    chinese_text, _ = _translate_with_aws(extracted_text, target_lang, native_lang)
                else:
                    chinese_text, _ = _translate_offline(extracted_text, target_lang, native_lang)
            else:
                english_text = trans_result
                chinese_text = extracted_text

            # ---- 容量检查 ----
            active_count = count_active()
            if active_count >= MAX_ACTIVE:
                return jsonify({
                    "error": "CARD_LIMIT",
                    "limit": MAX_ACTIVE,
                    "tier": "paid",
                    "message": "离线版已达200张卡片上限。请升级至付费版以解锁更多容量。",
                    "url": "https://www.cliplearn.ai/pricing"
                }), 409
            if active_count >= MAX_ACTIVE_FREE:
                return jsonify({
                    "error": "CARD_LIMIT_FREE",
                    "limit": MAX_ACTIVE_FREE,
                    "tier": "free",
                    "message": "免费版最多保存100张卡片。请使用网络版 ClipLearn，支持1000张卡片，同时多台设备数据同步。",
                    "url": "https://www.cliplearn.ai"
                }), 409

            # ---- 语音合成 ----
            os.makedirs(AUDIO_DIR, exist_ok=True)
            record_id = str(int(current_time))
            audio_path = os.path.join(AUDIO_DIR, "{}.mp3".format(record_id))
            male_audio_path = os.path.join(AUDIO_DIR, "{}_m.mp3".format(record_id))

            if aws_available:
                _speak_with_aws(english_text, audio_path)
            else:
                _speak_offline(english_text, audio_path)

            # 男声（始终用 Edge-TTS）
            try:
                _speak_male_offline(english_text, male_audio_path)
            except Exception:
                male_audio_path = None

            # ---- 存储 ----
            new_item = {
                "id": record_id,
                "english": english_text,
                "chinese": chinese_text,
                "audio_path": audio_path,
                "created_at": current_time,
            }

            insert_active(new_item)

            if status["token_remaining"] > 0:
                word_count = len(english_text.split())
                status["token_remaining"] = max(0, status["token_remaining"] - word_count)
                save_user_status(status)

            periodic_cleanup()

            response = send_file(audio_path, mimetype="audio/mp3")
            response.headers["X-English-Text"] = urllib.parse.quote(english_text)
            response.headers["X-Chinese-Text"] = urllib.parse.quote(chinese_text)
            response.headers["X-Record-Id"] = record_id
            response.headers["X-Has-Male-Audio"] = "1" if male_audio_path else "0"
            response.headers["Access-Control-Expose-Headers"] = \
                "X-English-Text, X-Chinese-Text, X-Record-Id, X-Has-Male-Audio"
            return response
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    # ── 4. 音频服务 ──
    @app.route("/api/audio/<record_id>", methods=['GET'])
    def serve_female_audio(record_id):
        """提供女声朗读的 MP3 文件"""
        female_path = os.path.join(AUDIO_DIR, "{}.mp3".format(record_id))
        if os.path.exists(female_path):
            return send_file(female_path, mimetype="audio/mp3")
        return jsonify({"error": "音频未生成"}), 404

    @app.route("/api/audio/<record_id>/male", methods=['GET'])
    def serve_male_audio(record_id):
        """提供男声朗读的 MP3 文件"""
        male_path = os.path.join(AUDIO_DIR, "{}_m.mp3".format(record_id))
        if os.path.exists(male_path):
            return send_file(male_path, mimetype="audio/mp3")
        return jsonify({"error": "男声音频未生成"}), 404

    # ── 5. 回收站 ──
    @app.route("/api/trash", methods=['GET'])
    def list_trash():
        """列出所有回收站记录"""
        conn = get_db_connection()
        rows = conn.execute(
            "SELECT * FROM trash_records ORDER BY trashed_at DESC"
        ).fetchall()
        conn.close()
        return jsonify([dict(r) for r in rows])

    @app.route("/api/trash/<record_id>", methods=['DELETE'])
    def trash_record(record_id):
        if delete_from_active_or_archive(record_id):
            return jsonify({"status": "ok", "message": "已移入垃圾桶"})
        return jsonify({"error": "记录不存在"}), 404

    @app.route("/api/recycle/<record_id>", methods=['DELETE'])
    def recycle_to_windows(record_id):
        """删除卡片：音频文件 -> Windows 回收站，记录 -> trash_records"""
        conn = get_db_connection()
        row = None
        for table in ("active_records", "archive_records"):
            row = conn.execute(
                "SELECT * FROM {} WHERE id=?".format(table), (record_id,)
            ).fetchone()
            if row:
                conn.execute("DELETE FROM {} WHERE id=?".format(table), (record_id,))
                break
        if not row:
            conn.close()
            return jsonify({"error": "记录不存在"}), 404

        # 移入 trash_records
        conn.execute(
            "INSERT OR REPLACE INTO trash_records "
            "(id, english, chinese, audio_path, created_at, trashed_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (row["id"], row["english"], row["chinese"], row["audio_path"],
             row["created_at"], time.time())
        )
        conn.commit()
        conn.close()

        # 生成卡片文本文件，与音频一起送 Windows 回收站
        txt_path = os.path.join(AUDIO_DIR, "{}.txt".format(record_id))
        try:
            with open(txt_path, 'w', encoding='utf-8') as f:
                f.write("ClipLearn Card #{}\n".format(record_id))
                f.write("{}\n".format('=' * 40))
                f.write("English:\n{}\n\n".format(row['english']))
                f.write("Chinese:\n{}\n".format(row['chinese']))
        except OSError:
            pass

        # 送音频 + 文本文件到 Windows 回收站
        for suffix in (".mp3", "_m.mp3", ".txt"):
            fp = os.path.join(AUDIO_DIR, "{}{}".format(record_id, suffix))
            if os.path.exists(fp):
                try:
                    send2trash(fp)
                except OSError:
                    pass

        return jsonify({"status": "ok", "message": "已移入 Windows 回收站"})

    @app.route("/api/trash/<record_id>/restore", methods=['POST'])
    def restore_trash_record(record_id):
        """从回收站恢复到 active_records"""
        conn = get_db_connection()
        row = conn.execute(
            "SELECT * FROM trash_records WHERE id=?", (record_id,)
        ).fetchone()
        if not row:
            conn.close()
            return jsonify({"error": "回收站中未找到该记录"}), 404

        # 容量检查
        active_count = conn.execute("SELECT COUNT(*) FROM active_records").fetchone()[0]
        if active_count >= MAX_ACTIVE:
            conn.close()
            return jsonify({
                "error": "CARD_LIMIT",
                "limit": MAX_ACTIVE,
                "message": "活跃卡片已达上限{}张，请先删除一些卡片后再恢复。".format(MAX_ACTIVE)
            }), 409

        conn.execute(
            "INSERT OR REPLACE INTO active_records "
            "(id, english, chinese, audio_path, created_at) VALUES (?, ?, ?, ?, ?)",
            (row["id"], row["english"], row["chinese"], row["audio_path"], row["created_at"])
        )
        conn.execute("DELETE FROM trash_records WHERE id=?", (record_id,))
        conn.commit()
        conn.close()
        return jsonify({"status": "ok", "message": "已恢复到原位置"})

    @app.route("/api/trash/<record_id>/permanent", methods=['DELETE'])
    def permanent_delete(record_id):
        """永久删除回收站记录及音频文件"""
        conn = get_db_connection()
        row = conn.execute(
            "SELECT * FROM trash_records WHERE id=?", (record_id,)
        ).fetchone()
        if not row:
            conn.close()
            return jsonify({"error": "记录不存在"}), 404

        # 删除音频文件
        for suffix in (".mp3", "_m.mp3"):
            try:
                fp = os.path.join(AUDIO_DIR, "{}{}".format(record_id, suffix))
                if os.path.exists(fp):
                    os.remove(fp)
            except OSError:
                pass

        conn.execute("DELETE FROM trash_records WHERE id=?", (record_id,))
        conn.commit()
        conn.close()
        return jsonify({"status": "ok", "message": "已永久删除"})

    # ── 6. 充值与状态 ──
    @app.route("/api/recharge", methods=['POST'])
    def recharge_tokens():
        status = get_user_status()
        data = request.get_json(silent=True) or {}
        amount = data.get('amount', 5000)
        status["token_remaining"] = status.get("token_remaining", 0) + amount
        status["buffer_start_time"] = None
        save_user_status(status)
        return jsonify({"status": "ok", "token_remaining": status["token_remaining"]})

    @app.route("/api/status_full", methods=['GET'])
    def api_status_full():
        return jsonify(get_user_status())

    # ── 7. 检查更新 ──
    @app.route("/api/check-update", methods=['GET'])
    def check_update():
        """代理到统计服务器查询最新版本"""
        try:
            req = urllib.request.Request(
                "{}/api/releases/latest?current={}".format(CLIPLEARN_SERVER, CLIPLEARN_VERSION),
                headers={"User-Agent": "ClipLearn/1.0"},
            )
            with urllib.request.urlopen(req, timeout=5) as resp:
                return jsonify(json.loads(resp.read().decode()))
        except Exception as e:
            return jsonify({
                "latest": CLIPLEARN_VERSION,
                "update_available": False,
                "error": str(e)
            })

    # ── 8. 留言反馈 ──
    @app.route("/api/feedback", methods=["POST"])
    def proxy_feedback():
        """代理用户留言到统计服务器"""
        data = request.get_json(silent=True) or {}
        data["machine_id"] = _get_machine_id()
        data["hostname"] = socket.gethostname()
        data["version"] = CLIPLEARN_VERSION
        try:
            body = json.dumps(data).encode("utf-8")
            req = urllib.request.Request(
                "{}/api/feedback".format(CLIPLEARN_SERVER),
                data=body,
                headers={"Content-Type": "application/json", "User-Agent": "ClipLearn/1.0"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=5) as resp:
                return jsonify(json.loads(resp.read().decode()))
        except Exception:
            return jsonify({"status": "ok", "message": "已保存（将在联网后发送）"})
