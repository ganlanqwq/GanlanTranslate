"""VR 附属程序自己的设置（手腕显示什么、位置、大小）。存在 vr_overlay/settings.json。

翻译相关的设置（模式启停、译文语言、桌面/VR）归主程序管，通过 HTTP 改，不存在这里。
"""

from __future__ import annotations

import json
import os
import sys
import threading

if getattr(sys, 'frozen', False):
    SETTINGS_PATH = os.path.join(os.path.dirname(sys.executable), 'vr_overlay', 'settings.json')
else:
    SETTINGS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'settings.json')

WRIST_CONTENTS = ('others', 'self', 'screen', 'off')

UI_LANGUAGE_CODES = ('zh-CN', 'en', 'ja', 'ko')

DEFAULTS = {
    'ui_language': '',        # 附属程序界面语言；空 = 跟主程序（默认系统语言）
    'left_wrist': 'others',
    'right_wrist': 'self',
    'wrist_width': 0.12,      # 米
    'wrist_along': 0.12,      # 沿手臂往手肘方向离手柄原点多远（米）
    'wrist_out': 0.045,       # 往手背方向偏多少（米）
    'wrist_up': -0.01,        # 上下（米）
    'wrist_roll': 0.0,        # 绕手臂再转多少度
    'wrist_alpha': 0.95,
    'wrist_font_scale': 1.0,  # 手腕卡片正文字号倍数
    'gaze_only': True,        # 抬腕看向手表时才显示
    'texture_upload': 'auto',  # auto = D3D11 贴图（不闪），失败自动退回；raw = 强制用 SetOverlayRaw（会闪，仅排查用）
}

LIMITS = {
    'wrist_width': (0.06, 0.25),
    'wrist_along': (-0.10, 0.30),
    'wrist_out': (-0.10, 0.15),
    'wrist_up': (-0.10, 0.10),
    'wrist_roll': (-180.0, 180.0),
    'wrist_alpha': (0.3, 1.0),
    'wrist_font_scale': (0.7, 1.6),
}


class Settings:
    def __init__(self, path: str = SETTINGS_PATH):
        self.path = path
        self._lock = threading.Lock()
        self.data = dict(DEFAULTS)
        self.version = 0
        self.load()

    def load(self) -> None:
        try:
            with open(self.path, encoding='utf-8') as f:
                raw = json.load(f)
        except Exception:
            return
        if isinstance(raw, dict):
            for key, default in DEFAULTS.items():
                if key in raw:
                    self.data[key] = self._clean(key, raw[key], default)

    def save(self) -> None:
        try:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            with open(self.path, 'w', encoding='utf-8') as f:
                json.dump(self.data, f, ensure_ascii=False, indent=2)
        except Exception as exc:
            print(f'[VR] 保存设置失败: {exc}')

    @staticmethod
    def _clean(key, value, default):
        if key in ('left_wrist', 'right_wrist'):
            return value if value in WRIST_CONTENTS else default
        if key == 'ui_language':
            return value if value in UI_LANGUAGE_CODES or value == '' else default
        if key == 'texture_upload':
            return value if value in ('auto', 'raw') else default
        if isinstance(default, bool):
            return bool(value)
        try:
            number = float(value)
        except (TypeError, ValueError):
            return default
        low, high = LIMITS.get(key, (number, number))
        return round(min(high, max(low, number)), 4)

    def get(self, key):
        return self.data.get(key, DEFAULTS.get(key))

    def set(self, key, value) -> None:
        with self._lock:
            cleaned = self._clean(key, value, DEFAULTS[key])
            if self.data.get(key) == cleaned:
                return
            self.data[key] = cleaned
            self.version += 1
        self.save()

    def adjust(self, key, delta) -> None:
        self.set(key, float(self.get(key)) + float(delta))

    def reset_wrist_pose(self) -> None:
        with self._lock:
            for key in ('wrist_width', 'wrist_along', 'wrist_out', 'wrist_up', 'wrist_roll'):
                self.data[key] = DEFAULTS[key]
            self.version += 1
        self.save()
