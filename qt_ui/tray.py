"""托盘图标和右键菜单。

2026-10-24 岚：托盘右键菜单换成和软件一样的样子，内容也按现在的功能来。以前是 pystray + Windows 自带的菜单
（打开控制面板 / 开启小面板 / 退出）。现在用 QSystemTrayIcon，右键弹出自己画的菜单（白底、绿色悬停、圆角、淡入）：

- 打开甘岚翻译（左键单击托盘图标也是）
- 自我翻译 / 翻译他人 / 翻译画面：点一下启动或停止，右边是状态
- 悬浮窗：自我 / 他人 / 屏幕 三个开关（显示方式是 VR 时不用悬浮窗，换成一行说明）
- 退出

模式的状态和启停走本机接口（/api/status、/api/modes/start|stop，和 VR 附属程序一样，Key 用网页推过来的那份）；
悬浮窗直接调 panel_launcher。菜单开着时每秒刷新一次状态，点了模式、悬浮窗菜单不关，看得到状态变化。
"""
from __future__ import annotations

import ctypes
import json
import threading
from typing import Callable, Optional
from urllib.request import ProxyHandler, Request, build_opener

from PySide6.QtCore import QEasingCurve, QObject, QPoint, QPropertyAnimation, QTimer, Qt, Signal
from PySide6.QtGui import QColor, QCursor, QGuiApplication, QIcon
from PySide6.QtWidgets import (
    QFrame, QGraphicsDropShadowEffect, QHBoxLayout, QLabel, QLayout, QPushButton, QSystemTrayIcon, QVBoxLayout, QWidget,
)

API = "http://127.0.0.1:5001/api"
MODES = ("self", "others", "screen")

# 颜色照 ui/static/css/style.css 的令牌
INK, INK_2, INK_3, INK_4 = "#12261A", "#3F5B49", "#7C9285", "#9AAFA1"
LINE, LINE_STRONG = "#E3EAE1", "#CBD9C7"
TINT = "#EEF5F0"
GREEN_50, GREEN_100, GREEN_400, GREEN_600, GREEN_700 = "#E4F2E8", "#CFE6D7", "#5FC08D", "#1F7A4D", "#17643E"
WARN, DANGER, DANGER_BG = "#B45309", "#C4463A", "#FCEDEB"

SHADOW = 14          # 卡片四周留给阴影的空白
WIDTH = 248

# 文字跟界面语言走（ui_locale：默认系统语言，网页里选过就跟网页）；用词和网页一样
_TEXT = {
    "open": {"zh-CN": "打开甘岚翻译", "en": "Open Ganlan Translate", "ja": "甘嵐翻訳を開く", "ko": "간란 번역 열기"},
    "mode.self": {"zh-CN": "自我翻译", "en": "Self translation", "ja": "自己翻訳", "ko": "자기 번역"},
    "mode.others": {"zh-CN": "翻译他人", "en": "Translate others", "ja": "他者翻訳", "ko": "타인 번역"},
    "mode.screen": {"zh-CN": "翻译画面", "en": "Translate screen", "ja": "画面翻訳", "ko": "화면 번역"},
    "state.running": {"zh-CN": "运行中", "en": "Running", "ja": "実行中", "ko": "실행 중"},
    "state.starting": {"zh-CN": "启动中…", "en": "Starting…", "ja": "起動中…", "ko": "시작 중…"},
    "state.stopping": {"zh-CN": "停止中…", "en": "Stopping…", "ja": "停止中…", "ko": "중지 중…"},
    "state.stopped": {"zh-CN": "未启动", "en": "Stopped", "ja": "停止", "ko": "중지됨"},
    "state.error": {"zh-CN": "出错", "en": "Error", "ja": "エラー", "ko": "오류"},
    "tip.start": {"zh-CN": "点一下启动", "en": "Click to start", "ja": "クリックで起動", "ko": "클릭하여 시작"},
    "tip.stop": {"zh-CN": "点一下停止", "en": "Click to stop", "ja": "クリックで停止", "ko": "클릭하여 중지"},
    "panels": {"zh-CN": "悬浮窗", "en": "Overlays", "ja": "フローティングウィンドウ", "ko": "플로팅 창"},
    "panel.self": {"zh-CN": "自我", "en": "Self", "ja": "自分", "ko": "자기"},
    "panel.others": {"zh-CN": "他人", "en": "Others", "ja": "他者", "ko": "타인"},
    "panel.screen": {"zh-CN": "屏幕", "en": "Screen", "ja": "画面", "ko": "화면"},
    "panels.vr": {
        "zh-CN": "VR 显示方式：字幕在 SteamVR 手腕上",
        "en": "VR mode: subtitles are on your SteamVR wrist",
        "ja": "VR モード：字幕は SteamVR の手首に表示",
        "ko": "VR 모드: 자막은 SteamVR 손목에 표시",
    },
    "quit": {"zh-CN": "退出", "en": "Quit", "ja": "終了", "ko": "종료"},
}

_FONTS = {"zh-CN": "Microsoft YaHei UI", "en": "Segoe UI", "ja": "Yu Gothic UI", "ko": "Malgun Gothic"}


def _lang() -> str:
    try:
        import ui_locale
        return ui_locale.current_ui_language()
    except Exception:
        return "zh-CN"


def _tr(key: str) -> str:
    texts = _TEXT[key]
    return texts.get(_lang()) or texts["zh-CN"]


def _font_family() -> str:
    # 样式表的 font-family 只认一个；缺的字由 Windows 字体回退补上
    return _FONTS.get(_lang(), "Microsoft YaHei UI")


def _animations_enabled() -> bool:
    """系统「显示动画」关了就不播（跟网页的减少动画一样）。"""
    try:
        value = ctypes.c_int(1)
        ctypes.windll.user32.SystemParametersInfoW(0x1042, 0, ctypes.byref(value), 0)  # SPI_GETCLIENTAREAANIMATION
        return bool(value.value)
    except Exception:
        return True


_opener = build_opener(ProxyHandler({}))   # 本机接口不走系统代理


def _api(path: str, body: Optional[dict] = None, timeout: float = 3.0) -> dict:
    data = None if body is None else json.dumps(body).encode("utf-8")
    req = Request(API + path, data=data, headers={"Content-Type": "application/json"},
                  method="GET" if body is None else "POST")
    with _opener.open(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8") or "{}")


def _mode_state(raw: str) -> str:
    raw = str(raw or "stopped")
    if raw.startswith("error"):
        return "error"
    return raw if raw in ("running", "starting", "stopping") else "stopped"


class _Item(QFrame):
    """菜单里能点的一行：悬停变浅绿，松开时触发。"""

    clicked = Signal()

    def __init__(self, parent=None, danger: bool = False):
        super().__init__(parent)
        self.setObjectName("dangerItem" if danger else "item")
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(34)
        self.layout_ = QHBoxLayout(self)
        self.layout_.setContentsMargins(10, 0, 10, 0)
        self.layout_.setSpacing(9)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self.rect().contains(event.position().toPoint()):
            self.clicked.emit()
        super().mouseReleaseEvent(event)

    # 悬停时里面的字也要变色（「退出」变红）：样式表里父控件的 :hover 不会让子控件重新套样式，用属性 hot
    def enterEvent(self, event):
        self._set_hot(True)
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._set_hot(False)
        super().leaveEvent(event)

    def _set_hot(self, hot: bool) -> None:
        self.setProperty("hot", hot)
        for w in [self, *self.findChildren(QLabel)]:
            w.style().unpolish(w)
            w.style().polish(w)


class TrayMenu(QWidget):
    """右键菜单。Qt.Popup：点菜单外面自动收起。"""

    open_requested = Signal()
    quit_requested = Signal()
    # 后台线程做完事用信号回到 Qt 主线程
    _status_ready = Signal(dict)
    _mode_done = Signal(str)
    _panel_done = Signal()

    def __init__(self, icon: QIcon):
        super().__init__(None, Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint | Qt.WindowType.NoDropShadowWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self._modes = {m: "stopped" for m in MODES}
        self._errors = {m: "" for m in MODES}
        self._pending = {}          # 刚点过、接口还没回来的模式：显示「启动中 / 停止中」
        self._display_mode = "desktop"
        self._fetching = False
        self._status_ready.connect(self._on_status)
        self._mode_done.connect(self._done)
        self._panel_done.connect(self._render_if_visible)
        self._timer = QTimer(self)
        self._timer.setInterval(1000)
        self._timer.timeout.connect(self._refresh)
        self._anim = None
        self._build(icon)

    # ---------- 界面 ----------

    def _build(self, icon: QIcon) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(SHADOW, SHADOW, SHADOW, SHADOW)
        outer.setSizeConstraint(QLayout.SizeConstraint.SetFixedSize)
        self._card = QFrame(self)
        self._card.setObjectName("card")
        self._card.setFixedWidth(WIDTH)
        shadow = QGraphicsDropShadowEffect(self._card)
        shadow.setBlurRadius(28)
        shadow.setOffset(0, 6)
        shadow.setColor(QColor(18, 38, 26, 46))
        self._card.setGraphicsEffect(shadow)
        outer.addWidget(self._card)

        box = QVBoxLayout(self._card)
        box.setContentsMargins(6, 6, 6, 6)
        box.setSpacing(1)

        self._open = _Item(self._card)
        logo = QLabel(self._open)
        logo.setPixmap(icon.pixmap(18, 18))
        logo.setFixedSize(18, 18)
        self._open_label = QLabel(self._open)
        self._open_label.setObjectName("strong")
        self._open.layout_.addWidget(logo)
        self._open.layout_.addWidget(self._open_label, 1)
        self._open.clicked.connect(self._on_open)
        box.addWidget(self._open)
        box.addWidget(self._separator())

        self._mode_rows = {}
        for mode in MODES:
            row = _Item(self._card)
            dot = QLabel(row)
            dot.setFixedSize(8, 8)
            name = QLabel(row)
            state = QLabel(row)
            state.setObjectName("state")
            row.layout_.addWidget(dot)
            row.layout_.addWidget(name, 1)
            row.layout_.addWidget(state)
            row.clicked.connect(lambda m=mode: self._toggle_mode(m))
            self._mode_rows[mode] = (row, dot, name, state)
            box.addWidget(row)
        box.addWidget(self._separator())

        self._panels_label = QLabel(self._card)
        self._panels_label.setObjectName("section")
        box.addWidget(self._panels_label)
        self._chips_row = QWidget(self._card)
        chips = QHBoxLayout(self._chips_row)
        chips.setContentsMargins(8, 2, 8, 6)
        chips.setSpacing(6)
        self._chips = {}
        for role in MODES:
            chip = QPushButton(self._chips_row)
            chip.setObjectName("chip")
            chip.setCheckable(True)
            chip.setCursor(Qt.CursorShape.PointingHandCursor)
            chip.setFixedHeight(28)
            chip.clicked.connect(lambda _=False, r=role: self._toggle_panel(r))
            chips.addWidget(chip, 1)
            self._chips[role] = chip
        box.addWidget(self._chips_row)
        self._vr_hint = QLabel(self._card)
        self._vr_hint.setObjectName("hint")
        self._vr_hint.setWordWrap(True)
        box.addWidget(self._vr_hint)
        box.addWidget(self._separator())

        self._quit = _Item(self._card, danger=True)
        power = QLabel("", self._quit)   # Segoe MDL2 Assets：电源
        power.setObjectName("glyph")
        power.setFixedWidth(18)
        power.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._quit_label = QLabel(self._quit)
        self._quit.layout_.addWidget(power)
        self._quit.layout_.addWidget(self._quit_label, 1)
        self._quit.clicked.connect(self._on_quit)
        box.addWidget(self._quit)

    def _separator(self) -> QFrame:
        line = QFrame(self._card)
        line.setObjectName("sep")
        line.setFixedHeight(1)
        wrap = QWidget(self._card)
        lay = QVBoxLayout(wrap)
        lay.setContentsMargins(4, 4, 4, 4)
        lay.addWidget(line)
        return wrap

    def _apply_style(self) -> None:
        self.setStyleSheet(f"""
            QWidget {{ font-family: {_font_family()}; font-size: 13px; color: {INK}; }}
            #card {{ background: #FFFFFF; border: 1px solid {LINE}; border-radius: 10px; }}
            #item, #dangerItem {{ background: transparent; border-radius: 6px; }}
            #item[hot="true"] {{ background: {TINT}; }}
            #dangerItem[hot="true"] {{ background: {DANGER_BG}; }}
            #dangerItem[hot="true"] QLabel {{ color: {DANGER}; }}
            QLabel {{ background: transparent; }}
            #strong {{ font-weight: 600; }}
            #state {{ font-size: 12px; color: {INK_3}; }}
            #section {{ font-size: 12px; color: {INK_3}; padding: 4px 10px 2px 10px; }}
            #hint {{ font-size: 12px; color: {INK_3}; padding: 2px 10px 6px 10px; }}
            #glyph {{ font-family: "Segoe Fluent Icons", "Segoe MDL2 Assets"; font-size: 13px; color: {INK_2}; }}
            #sep {{ background: {LINE}; }}
            #chip {{
                background: #FFFFFF; border: 1px solid {LINE_STRONG}; border-radius: 14px;
                color: {INK_2}; font-size: 12.5px; padding: 0 8px;
            }}
            #chip:hover {{ border-color: {GREEN_400}; }}
            #chip:checked {{ background: {GREEN_50}; border-color: {GREEN_100}; color: {GREEN_700}; font-weight: 600; }}
            #chip:checked:hover {{ border-color: {GREEN_400}; }}
        """)

    def _render(self) -> None:
        self._open_label.setText(_tr("open"))
        colors = {"running": GREEN_400, "starting": "#E2A33B", "stopping": "#E2A33B", "error": DANGER, "stopped": LINE_STRONG}
        state_colors = {"running": GREEN_600, "starting": WARN, "stopping": WARN, "error": DANGER, "stopped": INK_3}
        for mode, (row, dot, name, state_label) in self._mode_rows.items():
            state = self._pending.get(mode) or self._modes[mode]
            name.setText(_tr("mode." + mode))
            state_label.setText(_tr("state." + state))
            state_label.setStyleSheet(f"color: {state_colors[state]};")
            dot.setStyleSheet(f"background: {colors[state]}; border-radius: 4px;")
            tip = _tr("tip.stop" if state in ("running", "starting") else "tip.start")
            row.setToolTip(self._errors[mode] + "\n" + tip if state == "error" and self._errors[mode] else tip)
        self._panels_label.setText(_tr("panels"))
        vr = self._display_mode == "vr"
        self._chips_row.setVisible(not vr)
        self._vr_hint.setVisible(vr)
        self._vr_hint.setText(_tr("panels.vr"))
        open_roles = self._open_roles()
        for role, chip in self._chips.items():
            on = role in open_roles
            chip.setChecked(on)
            chip.setText(("✓ " if on else "") + _tr("panel." + role))
        self._quit_label.setText(_tr("quit"))

    # ---------- 显示 ----------

    def popup_at_cursor(self) -> None:
        self._apply_style()
        self._render()
        self._refresh()
        self.adjustSize()
        pos = QCursor.pos()
        screen = QGuiApplication.screenAt(pos) or QGuiApplication.primaryScreen()
        area = screen.availableGeometry()
        w, h = self.width(), self.height()
        # 托盘一般在右下角：菜单的右下角对着鼠标；任务栏在上边 / 左边时反过来
        x = pos.x() - w + SHADOW if pos.x() > area.center().x() else pos.x() - SHADOW
        y = pos.y() - h + SHADOW if pos.y() > area.center().y() else pos.y() - SHADOW
        x = max(area.left() - SHADOW, min(x, area.right() - w + SHADOW))
        y = max(area.top() - SHADOW, min(y, area.bottom() - h + SHADOW))
        above = pos.y() > area.center().y()
        self._timer.start()
        if _animations_enabled():
            self.setWindowOpacity(0.0)
            self.move(x, y + (6 if above else -6))
            self.show()
            fade = QPropertyAnimation(self, b"windowOpacity", self)
            fade.setDuration(140)
            fade.setStartValue(0.0)
            fade.setEndValue(1.0)
            slide = QPropertyAnimation(self, b"pos", self)
            slide.setDuration(180)
            slide.setStartValue(self.pos())
            slide.setEndValue(QPoint(x, y))
            slide.setEasingCurve(QEasingCurve.Type.OutCubic)
            fade.start()
            slide.start()
            self._anim = (fade, slide)
        else:
            self.setWindowOpacity(1.0)
            self.move(x, y)
            self.show()
        self.raise_()
        self.activateWindow()
        try:
            ctypes.windll.user32.SetForegroundWindow(int(self.winId()))   # 不然 Esc、点外面收起不灵
        except Exception:
            pass

    def hideEvent(self, event):
        self._timer.stop()
        super().hideEvent(event)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self.hide()
            return
        super().keyPressEvent(event)

    # ---------- 状态 ----------

    @staticmethod
    def _open_roles() -> set:
        try:
            from panel_launcher import running_roles
            return running_roles()
        except Exception:
            return set()

    def _refresh(self) -> None:
        if self._fetching:
            return
        self._fetching = True

        def work():
            try:
                status = _api("/status", timeout=2.0)
            except Exception:
                status = {}
            self._status_ready.emit(status)

        threading.Thread(target=work, name="ganlan-tray-status", daemon=True).start()

    def _on_status(self, status: dict) -> None:
        self._fetching = False
        modes = status.get("modes") or {}
        for mode in MODES:
            raw = str(modes.get(mode, "stopped"))
            self._modes[mode] = _mode_state(raw)
            self._errors[mode] = raw.split(":", 1)[1].strip() if raw.startswith("error") and ":" in raw else ""
        self._display_mode = str(status.get("display_mode") or "desktop")
        if self.isVisible():
            self._render()

    # ---------- 点击 ----------

    def _on_open(self) -> None:
        self.hide()
        self.open_requested.emit()

    def _on_quit(self) -> None:
        self.hide()
        self.quit_requested.emit()

    def _toggle_mode(self, mode: str) -> None:
        if mode in self._pending:
            return
        stop = self._modes[mode] in ("running", "starting")
        self._pending[mode] = "stopping" if stop else "starting"
        self._render()

        def work():
            try:
                if stop:
                    _api("/modes/stop", {"modes": [mode]}, timeout=70.0)
                else:
                    _api("/modes/start", {"modes": [mode], "api_keys": {}}, timeout=70.0)
            except Exception as exc:
                print(f"[tray] {'停止' if stop else '启动'} {mode} 失败: {exc!r}")
            self._mode_done.emit(mode)

        threading.Thread(target=work, name="ganlan-tray-mode", daemon=True).start()

    def _done(self, mode: str) -> None:
        self._pending.pop(mode, None)
        self._fetching = False
        self._refresh()

    def _toggle_panel(self, role: str) -> None:
        opened = role in self._open_roles()

        def work():
            try:
                from panel_launcher import close_panel, launch_panel_like_last
                if opened:
                    close_panel(role)
                else:
                    launch_panel_like_last(role)
            except Exception as exc:
                print(f"[tray] 切换悬浮窗 {role} 失败: {exc!r}")
            self._panel_done.emit()

        # 先按点过之后的样子画，进程起来 / 关掉要一会儿
        chip = self._chips[role]
        chip.setChecked(not opened)
        chip.setText(("" if opened else "✓ ") + _tr("panel." + role))
        threading.Thread(target=work, name="ganlan-tray-panel", daemon=True).start()

    def _render_if_visible(self) -> None:
        if self.isVisible():
            self._render()


class TrayIcon(QObject):
    """托盘图标：左键打开主窗口，右键弹 TrayMenu。"""

    def __init__(self, title: str, icon: QIcon, on_open: Callable[[], None], on_quit: Callable[[], None]):
        super().__init__()
        self._title = title
        self._icon_image = icon
        self._on_open = on_open
        self._on_quit = on_quit
        self._menu = TrayMenu(icon)
        self._menu.open_requested.connect(self._open)
        self._menu.quit_requested.connect(self._quit)
        self._icon = QSystemTrayIcon(icon)
        self._icon.setToolTip(title)
        self._icon.activated.connect(self._on_activated)
        self._icon.show()

    def _on_activated(self, reason) -> None:
        if reason == QSystemTrayIcon.ActivationReason.Context:
            self._menu.popup_at_cursor()
        elif reason in (QSystemTrayIcon.ActivationReason.Trigger, QSystemTrayIcon.ActivationReason.DoubleClick):
            self._open()

    def _open(self) -> None:
        try:
            self._on_open()
        except Exception as exc:
            print(f"[tray] 打开主窗口失败: {exc!r}")

    def _quit(self) -> None:
        try:
            self._on_quit()
        except Exception as exc:
            print(f"[tray] 退出失败: {exc!r}")

    def notify(self, message: str) -> None:
        """托盘弹一条系统通知（失败就算了）。"""
        try:
            self._icon.showMessage(self._title, message, self._icon_image, 5000)
        except Exception:
            pass

    def hide(self) -> None:
        self._menu.hide()
        self._icon.hide()


def create_tray(title: str, icon: QIcon, on_open: Callable[[], None], on_quit: Callable[[], None]) -> Optional[TrayIcon]:
    """系统没有托盘区时返回 None（这时主窗口按 × 直接退出）。要在 Qt 主线程、QApplication 建好以后调用。"""
    if not QSystemTrayIcon.isSystemTrayAvailable():
        return None
    return TrayIcon(title, icon, on_open, on_quit)
