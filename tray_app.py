"""系统托盘支持

在 Windows 下运行一个系统托盘图标，让应用最小化到托盘而不是被误关。

功能：
- 启动时显示托盘图标（使用角色图标）
- 菜单：打开控制面板 / 退出
- 隐藏控制台窗口（如果以控制台模式运行）
- 拦截控制台窗口的关闭按钮 → 最小化到托盘而不是退出
"""
from __future__ import annotations

import os
import sys
import threading
from typing import Callable, Optional

import pystray
from PIL import Image


# 托盘菜单文字跟界面语言走（ui_locale：默认系统语言，网页里选过就跟网页）
_TRAY_TEXT = {
    "open_main": {"zh-CN": "打开控制面板", "en": "Open control panel", "ja": "コントロールパネルを開く", "ko": "제어판 열기"},
    "open_panel": {"zh-CN": "开启小面板", "en": "Open mini panel", "ja": "ミニパネルを開く", "ko": "미니 패널 열기"},
    "close_panel": {"zh-CN": "关闭小面板", "en": "Close mini panel", "ja": "ミニパネルを閉じる", "ko": "미니 패널 닫기"},
    "quit": {"zh-CN": "退出", "en": "Quit", "ja": "終了", "ko": "종료"},
}


def _tr(key: str) -> str:
    try:
        import ui_locale
        return ui_locale.pick(_TRAY_TEXT[key])
    except Exception:
        return _TRAY_TEXT[key]["zh-CN"]


class TrayApp:
    """系统托盘控制器"""

    def __init__(
        self,
        on_open: Optional[Callable[[], None]] = None,
        on_quit: Optional[Callable[[], None]] = None,
        on_toggle_panel: Optional[Callable[[], None]] = None,
        on_panel_status: Optional[Callable[[], bool]] = None,
        icon_path: Optional[str] = None,
        title: str = "甘岚翻译",
    ) -> None:
        self._on_open = on_open
        self._on_quit = on_quit
        self._on_toggle_panel = on_toggle_panel
        self._on_panel_status = on_panel_status
        self._title = title
        self._icon: Optional[pystray.Icon] = None
        self._thread: Optional[threading.Thread] = None
        self._started = False
        self._quit_requested = False

        self._icon_image = self._load_icon(icon_path)

    def _load_icon(self, icon_path: Optional[str]):
        """加载托盘图标（从 icon.ico 或回退到内置角色图）"""
        candidates = []
        if icon_path and os.path.exists(icon_path):
            candidates.append(icon_path)
        if getattr(sys, "frozen", False):
            # PyInstaller 打包后资源路径
            base = getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
            candidates.append(os.path.join(base, "icon.ico"))
            candidates.append(os.path.join(os.path.dirname(sys.executable), "icon.ico"))
        # 源码模式
        candidates.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), "icon.ico"))

        for path in candidates:
            if path and os.path.exists(path):
                try:
                    img = Image.open(path)
                    # 转成 64x64 RGBA（托盘图标最佳尺寸）
                    img = img.convert("RGBA")
                    img.thumbnail((64, 64), Image.LANCZOS)
                    return img
                except Exception:
                    continue

        # 兜底：纯色圆
        fallback = Image.new("RGBA", (64, 64), (30, 86, 61, 255))
        return fallback

    def _on_open_clicked(self, icon: pystray.Icon, item: pystray.MenuItem) -> None:
        if self._on_open:
            try:
                self._on_open()
            except Exception:
                pass

    def _on_quit_clicked(self, icon: pystray.Icon, item: pystray.MenuItem) -> None:
        self._quit_requested = True
        try:
            icon.stop()
        except Exception:
            pass
        if self._on_quit:
            try:
                self._on_quit()
            except Exception:
                pass

    def _panel_is_running(self) -> bool:
        if self._on_panel_status:
            try:
                return bool(self._on_panel_status())
            except Exception:
                return False
        return False

    def _on_panel_toggle_clicked(self, icon: pystray.Icon, item: pystray.MenuItem) -> None:
        if self._on_toggle_panel:
            try:
                self._on_toggle_panel()
            except Exception:
                pass

    def _get_panel_menu_text(self, item: pystray.MenuItem) -> str:
        return _tr("close_panel" if self._panel_is_running() else "open_panel")

    def _get_panel_menu_checked(self, item: pystray.MenuItem) -> bool:
        return self._panel_is_running()

    def start(self) -> None:
        """启动托盘图标（非阻塞，运行在独立线程）"""
        if self._started:
            return
        self._started = True

        menu = pystray.Menu(
            pystray.MenuItem(lambda item: _tr("open_main"), self._on_open_clicked, default=True),
            pystray.MenuItem(
                self._get_panel_menu_text,
                self._on_panel_toggle_clicked,
                checked=self._get_panel_menu_checked,
            ),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(lambda item: _tr("quit"), self._on_quit_clicked),
        )

        self._icon = pystray.Icon(
            "ganlan_translate",
            icon=self._icon_image,
            title=self._title,
            menu=menu,
        )

        self._thread = threading.Thread(
            target=self._icon.run,
            name="ganlan-tray",
            daemon=True,
        )
        self._thread.start()

    def notify(self, message: str) -> None:
        """在托盘弹一条系统通知（失败就算了）。"""
        if self._icon is None:
            return
        try:
            self._icon.notify(message, self._title)
        except Exception:
            pass

    def stop(self) -> None:
        """停止托盘图标"""
        self._started = False
        if self._icon is not None:
            try:
                self._icon.stop()
            except Exception:
                pass

    def is_quit_requested(self) -> bool:
        return self._quit_requested
