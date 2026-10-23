"""在浏览器里预览安装界面（假的进度和日志，不会安装任何东西）。

用法（在仓库根目录）：.venv\\Scripts\\python.exe installer\\preview.py
然后打开 http://127.0.0.1:5098/ 。地址后面可以加：
    ?step=install     直接看安装那一步（动画区、进度、日志）
    ?step=finish      完成页
    ?mode=uninstall   卸载
    ?lang=ja          界面语言（zh-CN / en / ja / ko）
    ?upgrade=1        已经装过旧版
    ?running=1        甘岚翻译正在运行
    ?fail=1           装到一半出错
放进 installer/ui/animation/ 的动画刷新页面就能看到，不用打包。
"""
import mimetypes
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build  # noqa: E402

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 5098


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        path = unquote(urlsplit(self.path).path).lstrip('/') or 'setup.html'
        src = build.ui_files().get(path)   # 每次重新找：新放进去的文件也能看到
        if src is None or not src.is_file():
            self.send_error(404)
            return
        data = src.read_bytes()
        mime = mimetypes.guess_type(path)[0] or 'application/octet-stream'
        if mime.startswith('text/') or mime in ('application/javascript', 'application/json'):
            mime += '; charset=utf-8'
        self.send_response(200)
        self.send_header('Content-Type', mime)
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, fmt, *args):
        pass


if __name__ == '__main__':
    server = ThreadingHTTPServer(('127.0.0.1', PORT), Handler)
    print(f'安装界面预览：http://127.0.0.1:{PORT}/', flush=True)
    server.serve_forever()
