import sqlite3
import os
import sys
import json
import time
import threading

# ── 打包/开发路径解析 ──
if getattr(sys, 'frozen', False):
    # PyInstaller 打包后
    DATA_DIR = sys._MEIPASS
    APP_ROOT = os.path.join(os.environ.get('APPDATA', os.path.expanduser('~')), 'ClipLearn')
else:
    APP_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # 项目根目录
    DATA_DIR = APP_ROOT

DB_FILE = os.environ.get('CLIPLEARN_DB_PATH', os.path.join(APP_ROOT, "cliplearn.db"))
AUDIO_DIR = os.path.join(APP_ROOT, "clip_audios")

# 容量上限
MAX_ACTIVE_FREE = 100   # free tier: prompt to upgrade to web version
MAX_ACTIVE = 200        # hard limit: prompt to upgrade to paid version
MAX_ARCHIVE = 1000
CLEANUP_INTERVAL = 3600


def get_db_connection():
    """获取数据库连接，设置返回行字典格式并开启 WAL"""
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA wal_autocheckpoint=1000")
    return conn


def init_db():
    """初始化数据库，创建所有表"""
    conn = get_db_connection()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS active_records (
            id TEXT PRIMARY KEY,
            english TEXT NOT NULL DEFAULT '',
            chinese TEXT NOT NULL DEFAULT '',
            audio_path TEXT NOT NULL DEFAULT '',
            created_at REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS archive_records (
            id TEXT PRIMARY KEY,
            english TEXT NOT NULL DEFAULT '',
            chinese TEXT NOT NULL DEFAULT '',
            audio_path TEXT NOT NULL DEFAULT '',
            created_at REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS trash_records (
            id TEXT PRIMARY KEY,
            english TEXT NOT NULL DEFAULT '',
            chinese TEXT NOT NULL DEFAULT '',
            audio_path TEXT NOT NULL DEFAULT '',
            created_at REAL NOT NULL,
            trashed_at REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS user_status (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
    """)
    conn.commit()
    conn.close()
    print("数据库初始化成功")


# ── 用户状态 ──

def get_user_status():
    conn = get_db_connection()
    row = conn.execute("SELECT value FROM user_status WHERE key='status'").fetchone()
    conn.close()
    if row:
        return json.loads(row["value"])
    return {"token_remaining": 5000, "buffer_start_time": None}


def save_user_status(status):
    conn = get_db_connection()
    conn.execute(
        "INSERT OR REPLACE INTO user_status (key, value) VALUES ('status', ?)",
        (json.dumps(status),)
    )
    conn.commit()
    conn.close()


# ── 活跃记录操作 ──

def count_active():
    conn = get_db_connection()
    count = conn.execute("SELECT COUNT(*) FROM active_records").fetchone()[0]
    conn.close()
    return count


def get_active_records():
    conn = get_db_connection()
    records = [dict(r) for r in conn.execute(
        "SELECT * FROM active_records ORDER BY created_at ASC"
    ).fetchall()]
    conn.close()
    return records


def insert_active(record):
    conn = get_db_connection()
    conn.execute(
        "INSERT OR REPLACE INTO active_records (id, english, chinese, audio_path, created_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (record["id"], record["english"], record["chinese"], record["audio_path"], record["created_at"])
    )
    conn.commit()
    conn.close()


def move_oldest_active_to_archive():
    conn = get_db_connection()
    oldest = conn.execute("SELECT * FROM active_records ORDER BY created_at ASC LIMIT 1").fetchone()
    if oldest:
        conn.execute("DELETE FROM active_records WHERE id=?", (oldest["id"],))
        conn.execute(
            "INSERT OR REPLACE INTO archive_records (id, english, chinese, audio_path, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (oldest["id"], oldest["english"], oldest["chinese"], oldest["audio_path"], oldest["created_at"])
        )
    archive_count = conn.execute("SELECT COUNT(*) FROM archive_records").fetchone()[0]
    if archive_count > MAX_ARCHIVE:
        conn.execute(
            "DELETE FROM archive_records WHERE id IN "
            "(SELECT id FROM archive_records ORDER BY created_at ASC LIMIT 1)"
        )
    conn.commit()
    conn.close()


def delete_from_active_or_archive(record_id):
    conn = get_db_connection()
    for table in ("active_records", "archive_records"):
        row = conn.execute(
            "SELECT * FROM {} WHERE id=?".format(table), (record_id,)
        ).fetchone()
        if row:
            conn.execute("DELETE FROM {} WHERE id=?".format(table), (record_id,))
            conn.execute(
                "INSERT OR REPLACE INTO trash_records "
                "(id, english, chinese, audio_path, created_at, trashed_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (row["id"], row["english"], row["chinese"], row["audio_path"], row["created_at"], time.time())
            )
            conn.commit()
            conn.close()
            return True
    conn.close()
    return False


# ── 定时清理 ──

_last_cleanup = 0
_cleanup_lock = threading.Lock()


def periodic_cleanup():
    global _last_cleanup
    if not _cleanup_lock.acquire(blocking=False):
        return
    try:
        now = time.time()
        if now - _last_cleanup < CLEANUP_INTERVAL:
            return
        _last_cleanup = now

        # WAL checkpoint
        conn = get_db_connection()
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        conn.close()
    finally:
        _cleanup_lock.release()


def cleanup_invalid_cards():
    """清理失效卡片（音频文件在删除卡片时已送 Windows 回收站，此处为预留接口）。
    返回 (removed_active, removed_trash) 计数。
    """
    return 0, 0


if __name__ == '__main__':
    init_db()
