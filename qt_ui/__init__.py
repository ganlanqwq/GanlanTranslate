"""PySide6 应用入口。"""
from __future__ import annotations

import os
import sys
from typing import Callable, Optional

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtWidgets import QApplication

# QtWebEngine 要求在创建 QApplication 之前开启共享 OpenGL 上下文
QApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts, True)

from qt_ui.main_window import MainWindow, load_app_icon, release_persistent_profile  # noqa: E402


_app: Optional[QApplication] = None
_window: Optional[MainWindow] = None

# 销毁 profile 后等 Chromium 的存储线程把 localStorage 写完再结束事件循环
_STORAGE_FLUSH_WAIT_MS = 1200


class _ShowBridge(QObject):
    """托盘线程、Flask 线程都会要求显示窗口 / 退出；用信号转到 Qt 主线程再操作窗口。"""

    show_requested = Signal()
    quit_requested = Signal()


_bridge: Optional[_ShowBridge] = None
_quitting = False


def _quit_now() -> None:
    """正常关掉网页再退出事件循环。

    网页里的 Key 和设置存在 localStorage，Chromium 不会马上写盘（攒一会儿再写，写得多还限速，
    可能拖到一分钟）。以前退出时直接 os._exit，刚填的 Key 还没写盘就丢了（1.0 刚发时岚遇到：重开后 Key 没了）。
    先删页面、再删 profile，Chromium 销毁 profile 时会把没写的立即写完。
    """
    if _app is not None:
        _quit_now_into(_app)


def _quit_now_into(loop) -> None:
    """销毁网页和 profile，等存储写完后结束 loop（QApplication 或补写盘用的 QEventLoop）。"""
    global _quitting
    if _quitting:
        return
    _quitting = True
    if _window is not None:
        try:
            _window.release_web_view()
        except Exception:
            pass
    # 页面的 deleteLater 先处理掉，再删 profile
    QTimer.singleShot(100, release_persistent_profile)
    QTimer.singleShot(100 + _STORAGE_FLUSH_WAIT_MS, loop.quit)


def _show_now() -> None:
    if _window is None:
        return
    if _window.isMinimized():
        _window.showNormal()
    else:
        _window.show()
    _window.raise_()
    _window.activateWindow()
    # Windows 不让后台进程随便抢前台；第二个实例已调用 AllowSetForegroundWindow，这里再补一下
    try:
        import ctypes
        ctypes.windll.user32.SetForegroundWindow(int(_window.winId()))
    except Exception:
        pass


def show_window() -> bool:
    """可以在任何线程调用。窗口还没建好返回 False。"""
    if _bridge is None or _window is None:
        return False
    _bridge.show_requested.emit()
    return True


def request_quit() -> bool:
    """可以在任何线程调用：把网页存储写完后结束 Qt 事件循环（run_qt_app 随后返回）。
    Qt 还没起来返回 False，调用方自己结束进程。"""
    if _bridge is None or _app is None:
        return False
    _bridge.quit_requested.emit()
    return True


def run_qt_app(
    url: str = "http://127.0.0.1:5001/",
    on_quit: Optional[Callable[[], None]] = None,
    tray_title: Optional[str] = None,
    tray_hidden_message: Optional[Callable[[], str]] = None,
    **_kwargs,
) -> int:
    """阻塞运行 Qt 事件循环。返回进程退出码。

    tray_title：给了就建托盘图标（qt_ui.tray），按 × 只隐藏到托盘；托盘没起来时按 × 就退出。
    tray_hidden_message：第一次隐藏到托盘时弹的通知文字。
    """
    global _app, _window, _bridge

    os.environ.setdefault("QT_ENABLE_HIGHDPI_SCALING", "1")

    _app = QApplication.instance() or QApplication(sys.argv)
    # 这两个名字决定 QtWebEngine 的存储目录：%APPDATA%\GanlanTranslate\甘岚翻译\QtWebEngine\<profile>
    _app.setApplicationName("甘岚翻译")
    _app.setOrganizationName("GanlanTranslate")
    _app.setWindowIcon(load_app_icon())
    # 不让 Qt 在「最后一个窗口关了」时自己退出：隐藏到托盘时不能退；没有托盘时关窗口会走 on_quit → request_quit，
    # 由 _quit_now 先写完网页存储再退
    _app.setQuitOnLastWindowClosed(False)

    _bridge = _ShowBridge()
    _bridge.show_requested.connect(_show_now)
    _bridge.quit_requested.connect(_quit_now)

    tray = None
    if tray_title:
        try:
            from qt_ui.tray import create_tray
            tray = create_tray(tray_title, load_app_icon(), on_open=_show_now, on_quit=on_quit or _quit_now)
            print("已挂到系统托盘（关闭窗口会隐藏；托盘可再打开）" if tray else "[tray] 系统没有托盘区，关闭窗口就退出")
        except Exception as exc:
            print(f"[tray] 托盘启动失败（不影响主程序）: {exc!r}")

    hidden_notified = []

    def _on_hidden():
        # 第一次按 × 时提示一下：程序没退出，还在托盘里
        if tray is not None and tray_hidden_message and not hidden_notified:
            hidden_notified.append(True)
            tray.notify(tray_hidden_message())

    # 托盘没起来就别藏窗口，按 × 直接退出（不然藏起来找不回来）
    _window = MainWindow(url=url, on_quit=on_quit, hide_on_close=tray is not None, on_hidden=_on_hidden)
    _window.show()
    code = _app.exec()
    if not _quitting:
        # 别的原因结束了事件循环（比如注销 / 关机时系统让程序退出）：补一次写盘
        from PySide6.QtCore import QEventLoop
        loop = QEventLoop()
        _quit_now_into(loop)
        loop.exec()
    if tray is not None:
        tray.hide()   # 不然进程没了托盘里还留着图标，鼠标划过才消失
    return code
