# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec for ClipLearn Flask 后端服务器
打包成单个 exe 文件，输出到 build/ 目录
"""

import sys
import os
from pathlib import Path

# SPECPATH 是 PyInstaller 自动提供的 spec 文件所在目录
ROOT = Path(SPECPATH)

a = Analysis(
    [str(ROOT / 'server' / 'app.py')],
    pathex=[],
    binaries=[],
    datas=[
        # Flask 模板和静态文件
        (str(ROOT / 'server' / 'templates'), os.path.join('server', 'templates')),
        (str(ROOT / 'server' / 'static'), os.path.join('server', 'static')),
    ],
    hiddenimports=[
        # Flask 及其依赖
        'flask',
        'flask.app',
        'flask.helpers',
        'flask.json',
        'flask.signals',
        'flask_cors',
        'flask_cors.core',
        'flask_cors.decorator',
        # Jinja2 (Flask 模板引擎)
        'jinja2',
        'jinja2.ext',
        # Werkzeug
        'werkzeug',
        'werkzeug.serving',
        # OCR
        'pytesseract',
        # 图像处理
        'PIL',
        'PIL.Image',
        'PIL.ImageDraw',
        # 翻译
        'deep_translator',
        'deep_translator.google',
        # TTS
        'edge_tts',
        'edge_tts.list_voices',
        'edge_tts.communicate',
        'edge_tts.util',
        # 回收站
        'send2trash',
        'send2trash.win',
        # asyncio (edge_tts 需要)
        'asyncio',
        'concurrent.futures',
        # 标准库 (确保打包)
        'json',
        'sqlite3',
        'threading',
        'http',
        'http.server',
        'urllib',
        'ssl',
        'socket',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # 排除不需要的大型库
        'tkinter',
        'matplotlib',
        'numpy',
        'scipy',
        'pandas',
        'test',
        'unittest',
        'email',
        'html',
        'xml',
        'pydoc',
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
)

# 过滤掉可能导致问题的二进制文件
a.binaries = [b for b in a.binaries if not b[0].startswith('api-ms-win')]

pyz = PYZ(a.pure, a.zipped_data)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='ClipLearn-server',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,   # 保留控制台窗口，方便调试
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
