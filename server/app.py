import sys
import os
import threading

# 确保 server 目录在 sys.path 中，支持 python server/app.py 方式启动
_SERVER_DIR = os.path.dirname(os.path.abspath(__file__))
if _SERVER_DIR not in sys.path:
    sys.path.insert(0, _SERVER_DIR)

from flask import Flask
from flask_cors import CORS
from database import init_db
from routes import register_routes, send_heartbeat


def create_app():
    # 明确指定 templates 和 static 的路径，确保打包后 Electron 也能正确找到
    if getattr(sys, 'frozen', False):
        # PyInstaller 冻结模式：资源在 _MEIPASS/server/ 下
        base_dir = os.path.join(sys._MEIPASS, 'server')
    else:
        base_dir = os.path.dirname(os.path.abspath(__file__))

    app = Flask(
        __name__,
        template_folder=os.path.join(base_dir, 'templates'),
        static_folder=os.path.join(base_dir, 'static')
    )

    # 允许跨域（供 Electron 或前端调试使用）
    CORS(app)

    # 初始化数据库表结构
    init_db()

    # 注册路由（包含 after_request 缓存控制）
    register_routes(app)

    return app


# 创建 app 实例供打包工具或服务器调用
app = create_app()

if __name__ == '__main__':
    # 后台发送装机心跳（不阻塞启动）
    threading.Thread(target=send_heartbeat, daemon=True).start()

    debug_mode = os.environ.get("FLASK_DEBUG", "0") == "1"
    print("ClipLearn 服务已启动，正在监听 5000 端口...")
    app.run(host='127.0.0.1', port=5000, debug=debug_mode, threaded=True)
