#!/usr/bin/env python3
"""
甘岚翻译 · 桌面版启动器（安装版的入口）

Flask 在后台线程提供 API 和网页；主界面是 PySide6 窗口（QtWebEngine 打开同一个网页）+ 托盘。
"""
import sys
import os
import threading
import time

# 安装版是无控制台的窗口程序（不会再有一点 × 就杀掉整个程序的黑色控制台），
# 这时 sys.stdout/stderr 是 None；换成空设备，输出照样进 log_buffer（网页日志）。
for _name in ("stdout", "stderr"):
    if getattr(sys, _name) is None:
        setattr(sys, _name, open(os.devnull, "w", encoding="utf-8"))


def _run_panel_mode():
    import panel_app
    panel_args = ['panel_app.py'] + sys.argv[2:]
    panel_app.main(panel_args)


def _quit_process():
    """托盘退出 / 没有托盘时关窗口：关小面板，让 Qt 把网页存储（Key 等）写完再退出。

    Qt 正常退出后 run_qt_app 返回，主线程最后 os._exit。Qt 没起来或卡住时这里兜底硬退出。
    """
    def _do_quit():
        try:
            from plugin_manager import plugin_manager
            plugin_manager.shutdown()   # 让插件的 teardown 有机会保存东西
        except Exception:
            pass
        try:
            from panel_launcher import close_all_panels
            closed = close_all_panels()
            if closed:
                print(f"[panel] 已随程序关闭 {closed} 个小面板")
        except Exception:
            pass
        try:
            from qt_ui import request_quit
            asked = request_quit()
        except Exception:
            asked = False
        if asked:
            time.sleep(8.0)   # 正常情况 1~2 秒内主线程就退出了，走不到下面
        os._exit(0)

    threading.Thread(target=_do_quit, name="ganlan-quit", daemon=True).start()


_APP_NAME = {"zh-CN": "甘岚翻译", "en": "Ganlan Translate", "ja": "甘嵐翻訳", "ko": "간란 번역"}
_MSG_ALREADY_RUNNING = {
    "zh-CN": "已有 甘岚翻译 实例在运行。\n请到右下角托盘打开已运行的实例。",
    "en": "Ganlan Translate is already running.\nOpen it from the tray icon in the bottom-right corner.",
    "ja": "甘嵐翻訳はすでに起動しています。\n右下のトレイアイコンから開いてください。",
    "ko": "간란 번역이 이미 실행 중입니다.\n오른쪽 아래 트레이 아이콘에서 여세요.",
}
_MSG_IN_TRAY = {
    "zh-CN": "甘岚翻译还在托盘里运行。右键托盘图标可以打开或退出。",
    "en": "Ganlan Translate is still running in the tray. Right-click the tray icon to open or quit.",
    "ja": "甘嵐翻訳はトレイで動作中です。トレイアイコンを右クリックして開く・終了できます。",
    "ko": "간란 번역은 트레이에서 계속 실행 중입니다. 트레이 아이콘을 오른쪽 클릭해 열거나 종료하세요.",
}
_MSG_PORT_IN_USE = {
    "zh-CN": "已经有一个甘岚翻译在运行（端口 5001 被占用）。\n请先关掉它再打开。",
    "en": "Another Ganlan Translate is already running (port 5001 is in use).\nClose it first, then try again.",
    "ja": "別の甘嵐翻訳が起動しています（ポート 5001 使用中）。\n先に閉じてから起動してください。",
    "ko": "다른 간란 번역이 이미 실행 중입니다 (포트 5001 사용 중).\n먼저 닫은 뒤 다시 실행하세요.",
}


def _ui_text(texts):
    """提示框和托盘跟界面语言走（启动时网页还没报语言，就是系统语言）。"""
    try:
        import ui_locale
        return ui_locale.pick(texts)
    except Exception:
        return texts["zh-CN"]


def _activate_existing_instance(timeout: float = 3.0) -> bool:
    """已经有一个在运行：让它把主窗口显示到最前面。成功返回 True。"""
    try:
        import ctypes
        ctypes.windll.user32.AllowSetForegroundWindow(-1)  # ASFW_ANY：允许那边抢前台
    except Exception:
        pass
    import json
    from urllib.request import Request, urlopen
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            req = Request("http://127.0.0.1:5001/api/show-window", data=b"{}",
                          headers={"Content-Type": "application/json"}, method="POST")
            with urlopen(req, timeout=1.5) as resp:
                return bool(json.loads(resp.read().decode("utf-8") or "{}").get("success"))
        except Exception:
            time.sleep(0.3)   # 那边可能刚启动，Flask 还没起来
    return False


def _show_message(texts) -> None:
    """在主线程弹提示框（阻塞到用户点确定），之后再退出。"""
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(0, _ui_text(texts), _ui_text(_APP_NAME), 0x00000040)
    except Exception:
        pass


def _wait_flask(url: str = "http://127.0.0.1:5001/api/features", timeout: float = 25.0) -> bool:
    from urllib.request import urlopen
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urlopen(url, timeout=1.5) as resp:
                if 200 <= getattr(resp, "status", 200) < 500:
                    return True
        except Exception:
            time.sleep(0.15)
    return False


def _run_desktop_ui_mode():
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), 'ui'))
    # 保证可 import qt_ui
    root = os.path.dirname(os.path.abspath(__file__))
    if root not in sys.path:
        sys.path.insert(0, root)

    try:
        from log_buffer import install as install_log_buffer
        install_log_buffer()
    except Exception:
        pass

    try:
        from single_instance import acquire as acquire_single_instance, release as release_single_instance
    except Exception:
        acquire_single_instance = lambda: True
        release_single_instance = lambda: None

    if not acquire_single_instance():
        # 只能同时运行一个：把已经在跑的那个窗口叫出来，自己退出
        print("已有 甘岚翻译 实例在运行，本实例将退出。")
        if not _activate_existing_instance():
            _show_message(_MSG_ALREADY_RUNNING)
        os._exit(0)

    from proxy_detector import refresh_system_proxy_env, print_proxy_info
    system_proxies = refresh_system_proxy_env()
    try:
        print_proxy_info(system_proxies)
    except TypeError:
        try:
            print_proxy_info()
        except Exception:
            pass

    from ui.app import app, control_port_in_use

    if control_port_in_use(5001):
        # 开发版 ui/app.py 不走上面的单实例锁，只能靠端口判断
        print("端口 5001 已被占用：已经有一个甘岚翻译在运行，本实例将退出。")
        if not _activate_existing_instance(timeout=1.0):
            _show_message(_MSG_PORT_IN_USE)
        os._exit(0)

    flask_error = []

    def _run_flask():
        try:
            app.run(host='127.0.0.1', port=5001, debug=False, use_reloader=False, threaded=True)
        except Exception as exc:
            flask_error.append(exc)
            print(f"[ui] Flask 启动失败: {exc!r}")

    threading.Thread(target=_run_flask, name="ganlan-flask", daemon=True).start()
    if not _wait_flask():
        print("[ui] 控制服务未在时限内就绪，仍尝试打开原生窗口。")
        if flask_error:
            print(f"[ui] Flask 错误: {flask_error[0]!r}")
    else:
        print("控制服务已就绪：http://127.0.0.1:5001")

    # 再次打开程序时（第二个实例调 /api/show-window）把这个窗口叫出来；
    # 安装程序升级前调 /api/quit，像托盘「退出」一样正常退出
    try:
        import ui.app as _ui_app_module
        from qt_ui import show_window as _qt_show_window
        _ui_app_module.set_show_window_handler(_qt_show_window)
        _ui_app_module.set_quit_handler(_quit_process)
    except Exception as exc:
        print(f"[ui] 注册显示窗口接口失败: {exc!r}")

    exit_code = 1
    try:
        from qt_ui import run_qt_app
        # 托盘图标和右键菜单在 qt_ui/tray.py（Qt 主线程里建）
        exit_code = run_qt_app(
            url="http://127.0.0.1:5001/",
            on_quit=_quit_process,
            tray_title=_ui_text(_APP_NAME),
            tray_hidden_message=lambda: _ui_text(_MSG_IN_TRAY),
        )
    except Exception as exc:
        print(f"[ui] PySide6 窗口启动失败: {exc!r}")
        import traceback
        traceback.print_exc()
        # 最后手段：阻塞保活 Flask，避免立刻退出
        while True:
            time.sleep(3600)
    finally:
        try:
            release_single_instance()
        except Exception:
            pass
    os._exit(exit_code or 0)


if __name__ == '__main__':
    if len(sys.argv) >= 2 and sys.argv[1] == '--panel-app':
        _run_panel_mode()
    else:
        _run_desktop_ui_mode()
