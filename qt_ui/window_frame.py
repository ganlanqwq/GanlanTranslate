"""主窗口不要系统标题栏（Windows）。

系统标题栏是白的，跟网页深绿的顶部接不上（2026-10-17 岚：窗口边框不好看，要无头窗口）。
做法：Qt 无边框窗口，再把 WS_CAPTION / WS_THICKFRAME 加回去，保留贴边吸附、最小化/最大化动画和
系统阴影；WM_NCCALCSIZE 把系统画的边框和标题栏去掉，WM_NCHITTEST 告诉系统哪里能拖、哪里能拉大小。
能拖的地方由网页告诉我们（window-chrome.js：网页最上面一条，去掉按钮）；最小化、最大化、关闭按钮也在网页里。
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
from typing import Callable

RESIZE_BORDER = 5                 # 窗口边缘这么宽（逻辑像素）的一圈可以拉大小

WM_NCCALCSIZE = 0x0083
WM_NCHITTEST = 0x0084
HTCAPTION = 2
HTLEFT, HTRIGHT, HTTOP, HTTOPLEFT, HTTOPRIGHT = 10, 11, 12, 13, 14
HTBOTTOM, HTBOTTOMLEFT, HTBOTTOMRIGHT = 15, 16, 17
GWL_STYLE = -16
WS_CAPTION = 0x00C00000
WS_THICKFRAME = 0x00040000
WS_SYSMENU = 0x00080000
WS_MINIMIZEBOX = 0x00020000
WS_MAXIMIZEBOX = 0x00010000
SWP_FLAGS = 0x0001 | 0x0002 | 0x0004 | 0x0010 | 0x0020  # NOSIZE NOMOVE NOZORDER NOACTIVATE FRAMECHANGED
SM_CXFRAME, SM_CYFRAME, SM_CXPADDEDBORDER = 32, 33, 92
ABM_GETSTATE, ABM_GETAUTOHIDEBAREX, ABS_AUTOHIDE = 0x4, 0xB, 0x1
ABE_LEFT, ABE_TOP, ABE_RIGHT, ABE_BOTTOM = 0, 1, 2, 3
MONITOR_DEFAULTTONEAREST = 2


class _NCCALCSIZE_PARAMS(ctypes.Structure):
    _fields_ = [("rgrc", wt.RECT * 3), ("lppos", ctypes.c_void_p)]


class _MARGINS(ctypes.Structure):
    _fields_ = [("cxLeftWidth", ctypes.c_int), ("cxRightWidth", ctypes.c_int),
                ("cyTopHeight", ctypes.c_int), ("cyBottomHeight", ctypes.c_int)]


class _APPBARDATA(ctypes.Structure):
    _fields_ = [("cbSize", wt.DWORD), ("hWnd", wt.HWND), ("uCallbackMessage", wt.UINT),
                ("uEdge", wt.UINT), ("rc", wt.RECT), ("lParam", wt.LPARAM)]


class _MONITORINFO(ctypes.Structure):
    _fields_ = [("cbSize", wt.DWORD), ("rcMonitor", wt.RECT), ("rcWork", wt.RECT), ("dwFlags", wt.DWORD)]


# 自己开一份 DLL 句柄再设 argtypes：ctypes.windll.user32 是全进程共用的，改了会影响别的模块
_user32 = ctypes.WinDLL("user32")
_user32.GetWindowLongW.restype = ctypes.c_long
_user32.SetWindowLongW.argtypes = [wt.HWND, ctypes.c_int, ctypes.c_long]
_user32.SetWindowPos.argtypes = [wt.HWND, wt.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wt.UINT]
_user32.MonitorFromWindow.restype = wt.HMONITOR
_user32.MonitorFromWindow.argtypes = [wt.HWND, wt.DWORD]
_user32.GetMonitorInfoW.argtypes = [wt.HMONITOR, ctypes.POINTER(_MONITORINFO)]
_shell32 = ctypes.WinDLL("shell32")
_shell32.SHAppBarMessage.restype = ctypes.c_size_t
_shell32.SHAppBarMessage.argtypes = [wt.DWORD, ctypes.POINTER(_APPBARDATA)]


def _signed_word(value: int) -> int:
    value &= 0xFFFF
    return value - 0x10000 if value & 0x8000 else value


def _frame_thickness(hwnd: int) -> tuple[int, int]:
    """最大化时系统会把窗口往屏幕外多放这么多（物理像素），客户区要缩回来。"""
    try:
        dpi = _user32.GetDpiForWindow(wt.HWND(hwnd))
        metric = lambda index: _user32.GetSystemMetricsForDpi(index, dpi)  # noqa: E731
    except AttributeError:
        metric = _user32.GetSystemMetrics
    padded = metric(SM_CXPADDEDBORDER)
    return metric(SM_CXFRAME) + padded, metric(SM_CYFRAME) + padded


def _autohide_taskbar_edges(hwnd: int) -> list[int]:
    """窗口所在屏幕上开了「自动隐藏」的任务栏在哪几条边。"""
    data = _APPBARDATA(cbSize=ctypes.sizeof(_APPBARDATA))
    if not (_shell32.SHAppBarMessage(ABM_GETSTATE, ctypes.byref(data)) & ABS_AUTOHIDE):
        return []
    info = _MONITORINFO(cbSize=ctypes.sizeof(_MONITORINFO))
    monitor = _user32.MonitorFromWindow(wt.HWND(hwnd), MONITOR_DEFAULTTONEAREST)
    if not monitor or not _user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
        return []
    edges = []
    for edge in (ABE_LEFT, ABE_TOP, ABE_RIGHT, ABE_BOTTOM):
        query = _APPBARDATA(cbSize=ctypes.sizeof(_APPBARDATA), uEdge=edge, rc=info.rcMonitor)
        if _shell32.SHAppBarMessage(ABM_GETAUTOHIDEBAREX, ctypes.byref(query)):
            edges.append(edge)
    return edges


def enable_native_frame(hwnd: int) -> None:
    """无边框窗口补回系统样式（贴边吸附、动画、Alt+空格菜单）和阴影。可以重复调用。"""
    style = _user32.GetWindowLongW(wt.HWND(hwnd), GWL_STYLE)
    wanted = style | WS_CAPTION | WS_THICKFRAME | WS_SYSMENU | WS_MINIMIZEBOX | WS_MAXIMIZEBOX
    if wanted != style:
        _user32.SetWindowLongW(wt.HWND(hwnd), GWL_STYLE, wanted)
    try:
        # 往客户区里伸 1px「玻璃」，系统才会给去掉了边框的窗口画阴影；这 1px 被网页盖住
        margins = _MARGINS(0, 0, 1, 0)
        ctypes.windll.dwmapi.DwmExtendFrameIntoClientArea(wt.HWND(hwnd), ctypes.byref(margins))
    except Exception:
        pass
    _user32.SetWindowPos(wt.HWND(hwnd), None, 0, 0, 0, 0, SWP_FLAGS)


def handle_frame_message(message, device_pixel_ratio: float, is_caption: Callable[[float, float], bool]):
    """在窗口的 nativeEvent 里调用。返回要交给系统的结果；None 表示照常交给 Qt。

    is_caption(x, y)：窗口内的逻辑坐标这一点能不能拖动窗口。
    """
    msg = wt.MSG.from_address(int(message))
    if msg.message == WM_NCCALCSIZE:
        if not msg.wParam:
            return None
        if _user32.IsZoomed(msg.hWnd):
            rect = _NCCALCSIZE_PARAMS.from_address(msg.lParam).rgrc[0]
            fx, fy = _frame_thickness(msg.hWnd)
            rect.left += fx
            rect.right -= fx
            rect.top += fy
            rect.bottom -= fy
            # 任务栏自动隐藏时那条边留 1px，不然鼠标移过去任务栏出不来
            for edge in _autohide_taskbar_edges(msg.hWnd):
                if edge == ABE_LEFT:
                    rect.left += 1
                elif edge == ABE_TOP:
                    rect.top += 1
                elif edge == ABE_RIGHT:
                    rect.right -= 1
                else:
                    rect.bottom -= 1
        # 整个窗口都是客户区：系统不再画边框和标题栏
        return 0

    if msg.message == WM_NCHITTEST:
        window_rect = wt.RECT()
        _user32.GetWindowRect(msg.hWnd, ctypes.byref(window_rect))
        x = _signed_word(msg.lParam) - window_rect.left
        y = _signed_word(msg.lParam >> 16) - window_rect.top
        width = window_rect.right - window_rect.left
        height = window_rect.bottom - window_rect.top
        if not (0 <= x < width and 0 <= y < height):
            return None
        dpr = device_pixel_ratio or 1.0

        if not _user32.IsZoomed(msg.hWnd):
            border = max(1, round(RESIZE_BORDER * dpr))
            left, right = x < border, x >= width - border
            top, bottom = y < border, y >= height - border
            if top and left:
                return HTTOPLEFT
            if top and right:
                return HTTOPRIGHT
            if bottom and left:
                return HTBOTTOMLEFT
            if bottom and right:
                return HTBOTTOMRIGHT
            if left:
                return HTLEFT
            if right:
                return HTRIGHT
            if top:
                return HTTOP
            if bottom:
                return HTBOTTOM

        # 系统当标题栏处理：拖动、拖到屏幕边吸附、双击最大化、右键窗口菜单
        if is_caption(x / dpr, y / dpr):
            return HTCAPTION
    return None
