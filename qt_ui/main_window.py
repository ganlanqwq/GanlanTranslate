"""甘岚翻译 · PySide6 主窗口

原生 Qt 窗口壳，里面用 QtWebEngine 打开 Flask 提供的控制页。
关闭窗口 → 隐藏到托盘；托盘可再打开。

Windows 上不要系统标题栏（2026-10-17 岚：要无头窗口）：网页铺满整个窗口，网页最上面一条能拖动窗口，
最小化 / 最大化 / 关闭按钮在网页右上角（ui/static/js/window-chrome.js），网页通过 QWebChannel 调这里。
"""
from __future__ import annotations

import json
import os
import sys
from typing import Callable, Optional

from PySide6.QtCore import QEvent, QFile, QIODevice, QObject, QPoint, QPointF, QRectF, QStandardPaths, QUrl, Qt, QTimer, Slot
from PySide6.QtGui import QCloseEvent, QIcon
from PySide6.QtWidgets import QFileDialog, QMainWindow, QVBoxLayout, QWidget

try:
    from PySide6.QtWebChannel import QWebChannel
    from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile, QWebEngineScript, QWebEngineSettings
    from PySide6.QtWebEngineWidgets import QWebEngineView
except ImportError as exc:  # pragma: no cover
    raise RuntimeError("需要 PySide6 QtWebEngine（完整版 PySide6 / Addons）") from exc

FRAMELESS = sys.platform == "win32"
if FRAMELESS:
    from qt_ui.window_frame import enable_native_frame, handle_frame_message

DEFAULT_UI_URL = "http://127.0.0.1:5001/"

# 没最大化时窗口四周 1px 的边
WINDOW_BORDER = "#2A4636"
# 网页还没告诉我们之前（还在加载），最上面这么高（逻辑像素）一条先当拖动区
DEFAULT_DRAG_HEIGHT = 32

_SAVE_DIALOG_TITLE = {"zh-CN": "保存文件", "en": "Save file", "ja": "ファイルを保存", "ko": "파일 저장"}
_APP_TITLE = {"zh-CN": "甘岚翻译", "en": "Ganlan Translate", "ja": "甘嵐翻訳", "ko": "간란 번역"}


def _app_title() -> str:
    try:
        import ui_locale
        return ui_locale.pick(_APP_TITLE)
    except Exception:
        return _APP_TITLE["zh-CN"]


def _save_dialog_title() -> str:
    try:
        import ui_locale
        return ui_locale.pick(_SAVE_DIALOG_TITLE)
    except Exception:
        return _SAVE_DIALOG_TITLE["zh-CN"]


def choose_download_path(parent, suggested_name: str) -> str:
    """弹「保存文件」对话框，默认放在「下载」文件夹。取消返回空串。"""
    start_dir = (
        QStandardPaths.writableLocation(QStandardPaths.StandardLocation.DownloadLocation)
        or os.path.expanduser("~")
    )
    path, _ = QFileDialog.getSaveFileName(
        parent, _save_dialog_title(), os.path.join(start_dir, suggested_name or "download"),
    )
    return path or ""


_PROFILE: Optional[QWebEngineProfile] = None


def persistent_profile() -> QWebEngineProfile:
    """网页存储落盘的 profile（Key、表单设置都在网页的 localStorage 里）。

    Qt 6 的 defaultProfile 是无痕的，以前用它时每次重开程序网页里填的 Key 都会丢。
    有名字的 profile 存在 %APPDATA%\\GanlanTranslate\\甘岚翻译\\QtWebEngine\\GanlanTranslate。
    挂在 QApplication 上，保证比页面活得久。
    """
    global _PROFILE
    if _PROFILE is None:
        from PySide6.QtWidgets import QApplication
        _PROFILE = QWebEngineProfile("GanlanTranslate", QApplication.instance())
    return _PROFILE


def release_persistent_profile() -> None:
    """退出前销毁 profile：Chromium 这时才会把还没写盘的 localStorage 立即写完。"""
    global _PROFILE
    if _PROFILE is not None:
        _PROFILE.deleteLater()
        _PROFILE = None


class _QuietPage(QWebEnginePage):
    """抑制无关控制台噪音，保留页面功能。"""

    def javaScriptConsoleMessage(self, level, message, lineNumber, sourceID):  # noqa: N802
        # 开发时可取消注释：
        # print(f"[web] {message} ({sourceID}:{lineNumber})")
        return


def _qwebchannel_script() -> Optional[QWebEngineScript]:
    """把 Qt 自带的 qwebchannel.js 注入网页，网页里才有 QWebChannel。"""
    source = QFile(":/qtwebchannel/qwebchannel.js")
    if not source.open(QIODevice.OpenModeFlag.ReadOnly):
        return None
    script = QWebEngineScript()
    script.setName("ganlan-qwebchannel")
    script.setSourceCode(bytes(source.readAll()).decode("utf-8"))
    source.close()
    script.setInjectionPoint(QWebEngineScript.InjectionPoint.DocumentCreation)
    script.setWorldId(QWebEngineScript.ScriptWorldId.MainWorld)
    script.setRunsOnSubFrames(False)
    return script


class _WindowBridge(QObject):
    """网页里的 window-chrome.js 通过 QWebChannel 调这些。"""

    def __init__(self, window: "MainWindow"):
        super().__init__(window)
        self._window = window

    @Slot()
    def minimize(self) -> None:
        self._window.showMinimized()

    @Slot()
    def toggleMaximize(self) -> None:  # noqa: N802
        self._window.toggle_maximized()

    @Slot()
    def closeWindow(self) -> None:  # noqa: N802
        self._window.close()

    @Slot(str)
    def setDragRegion(self, payload: str) -> None:  # noqa: N802
        self._window.set_drag_region(payload)

    @Slot(result=str)
    def state(self) -> str:
        return json.dumps(self._window.chrome_state())


class MainWindow(QMainWindow):
    def __init__(
        self,
        url: str = DEFAULT_UI_URL,
        on_quit: Optional[Callable[[], None]] = None,
        hide_on_close: bool = True,
        on_hidden: Optional[Callable[[], None]] = None,
        **_kwargs,
    ):
        super().__init__()
        self._on_quit = on_quit
        self._hide_on_close = hide_on_close
        self._on_hidden = on_hidden
        self._quit_requested = False
        self._url = url
        # 网页最上面能拖动窗口的那一条：高度 + 里面不能拖的按钮（网页坐标）
        self._drag_height = float(DEFAULT_DRAG_HEIGHT)
        self._drag_holes: list[QRectF] = []
        self.view: Optional[QWebEngineView] = None

        self.setWindowTitle(_app_title())
        self.resize(1240, 920)
        self.setMinimumSize(980, 720)
        self._set_icon()
        if FRAMELESS:
            self.setWindowFlags(self.windowFlags() | Qt.WindowType.FramelessWindowHint)

        # 窗口底色贴近杂志页，避免加载瞬间闪白
        self.setStyleSheet("QMainWindow { background: #0A2115; }")

        central = QWidget()
        central.setObjectName("windowFrame")
        layout = QVBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self._frame_layout = layout
        self._sync_frame_border()

        self.view = QWebEngineView(central)
        profile = persistent_profile()
        # QtWebEngine 默认把网页里的下载直接丢掉（导出预设会显示成功但没有文件），这里接住
        profile.downloadRequested.connect(self._on_download_requested)
        page = _QuietPage(profile, self.view)
        if FRAMELESS:
            self._bridge = _WindowBridge(self)
            self._channel = QWebChannel(page)
            self._channel.registerObject("ganlanWindow", self._bridge)
            page.setWebChannel(self._channel)
            script = _qwebchannel_script()
            if script is not None:
                page.scripts().insert(script)
        self.view.setPage(page)

        settings = self.view.settings()
        settings.setAttribute(QWebEngineSettings.WebAttribute.JavascriptEnabled, True)
        settings.setAttribute(QWebEngineSettings.WebAttribute.LocalStorageEnabled, True)
        settings.setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, True)
        settings.setAttribute(QWebEngineSettings.WebAttribute.ShowScrollBars, True)
        # 不显示默认右键「检查」类菜单感（仍保留输入框粘贴等）
        self.view.setContextMenuPolicy(Qt.ContextMenuPolicy.NoContextMenu)

        layout.addWidget(self.view)
        self.setCentralWidget(central)

        self.view.loadStarted.connect(self._reset_drag_region)
        self.view.loadFinished.connect(self._on_load_finished)
        self.view.setUrl(QUrl(self._url))

        if FRAMELESS:
            enable_native_frame(int(self.winId()))

        # 若首屏未就绪，短延迟再试一次
        QTimer.singleShot(2500, self._ensure_loaded)

    # --- 无标题栏 -----------------------------------------------------------
    def nativeEvent(self, event_type, message):  # noqa: N802
        if FRAMELESS and bytes(event_type) == b"windows_generic_MSG":
            result = handle_frame_message(message, self.devicePixelRatioF(), self._is_drag_point)
            if result is not None:
                return True, result
        return super().nativeEvent(event_type, message)

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        if FRAMELESS:
            enable_native_frame(int(self.winId()))

    def changeEvent(self, event) -> None:  # noqa: N802
        super().changeEvent(event)
        if not FRAMELESS or self.view is None:
            return
        if event.type() == QEvent.Type.WindowStateChange:
            self._sync_frame_border()
            self._push_chrome_state()
        elif event.type() == QEvent.Type.ActivationChange:
            self._push_chrome_state()

    def toggle_maximized(self) -> None:
        if self.isMaximized():
            self.showNormal()
        else:
            self.showMaximized()

    def chrome_state(self) -> dict:
        return {"maximized": self.isMaximized(), "active": self.isActiveWindow()}

    def set_drag_region(self, payload: str) -> None:
        """网页报来的拖动区：{"height": 32, "holes": [[x, y, w, h], ...]}（网页坐标，= 网页控件里的逻辑像素）。"""
        try:
            data = json.loads(payload)
            height = max(0.0, min(float(data.get("height", 0)), 200.0))
            holes = [QRectF(float(x), float(y), float(w), float(h)) for x, y, w, h in data.get("holes", [])[:200]]
        except (TypeError, ValueError):
            return
        self._drag_height = height
        self._drag_holes = holes

    def _reset_drag_region(self) -> None:
        self._drag_height = float(DEFAULT_DRAG_HEIGHT)
        self._drag_holes = []

    def _is_drag_point(self, x: float, y: float) -> bool:
        if self.view is None:
            return False
        point = self.view.mapFrom(self, QPoint(int(x), int(y)))
        if not self.view.rect().contains(point) or point.y() >= self._drag_height:
            return False
        local = QPointF(point)
        return not any(hole.contains(local) for hole in self._drag_holes)

    def _push_chrome_state(self) -> None:
        state = json.dumps(self.chrome_state())
        self.view.page().runJavaScript(
            f"window.ganlanWindowChrome && window.ganlanWindowChrome.setState({state});"
        )

    def _sync_frame_border(self) -> None:
        """没最大化时四周留 1px 的边（中间那层的底色就是边的颜色），最大化时贴满屏幕。"""
        frame = self._frame_layout.parentWidget()
        if not FRAMELESS:
            frame.setStyleSheet("#windowFrame { background: #0A2115; }")
            return
        width = 0 if self.isMaximized() or self.isFullScreen() else 1
        self._frame_layout.setContentsMargins(width, width, width, width)
        frame.setStyleSheet(f"#windowFrame {{ background: {WINDOW_BORDER}; }}")

    def _on_download_requested(self, download) -> None:
        """网页里的下载（导出预设等）：让用户选保存位置。"""
        path = choose_download_path(self, download.downloadFileName())
        if not path:
            download.cancel()
            return
        download.setDownloadDirectory(os.path.dirname(path))
        download.setDownloadFileName(os.path.basename(path))
        download.accept()

    def _set_icon(self) -> None:
        icon_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "icon.ico")
        if os.path.exists(icon_path):
            self.setWindowIcon(QIcon(icon_path))

    def _on_load_finished(self, ok: bool) -> None:
        if ok:
            # 去掉页面默认外边距感；内容已是杂志满幅
            self.view.page().runJavaScript(
                "document.documentElement.style.overflowY='auto';"
                "document.body && (document.body.style.margin='0');"
            )
            self.setStyleSheet("QMainWindow { background: #FBFCFA; }")
        else:
            QTimer.singleShot(1200, lambda: self.view.setUrl(QUrl(self._url)))

    def _ensure_loaded(self) -> None:
        if self.view.url().toString() in ("", "about:blank"):
            self.view.setUrl(QUrl(self._url))

    def closeEvent(self, event: QCloseEvent) -> None:
        if self._quit_requested:
            event.accept()
            return
        if not self._hide_on_close:
            # 托盘没起来：隐藏了就找不回来，按 × 直接退出
            event.accept()
            self._quit_requested = True
            if self._on_quit:
                self._on_quit()
            return
        # 按 × 只隐藏到托盘，程序继续运行；从托盘「打开控制面板」或再次打开程序都能叫回来
        self.hide()
        event.ignore()
        if self._on_hidden:
            try:
                self._on_hidden()
            except Exception:
                pass

    def release_web_view(self) -> None:
        """退出前销毁网页（页面是 view 的子对象，一起删），profile 要等页面没了再删。"""
        self._quit_requested = True
        self.hide()
        self.view.deleteLater()


def load_app_icon() -> QIcon:
    icon_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "icon.ico")
    if os.path.exists(icon_path):
        return QIcon(icon_path)
    return QIcon()
