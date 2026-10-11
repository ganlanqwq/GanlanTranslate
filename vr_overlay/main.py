"""甘岚翻译 VR 附属程序（SteamVR overlay）。

- 第一次运行时把自己注册进 SteamVR 并开启自动启动，之后 SteamVR 一启动就会拉起它。
- 两个手腕各挂一张卡片（跟着左右手柄），显示 自我 / 他人 / 屏幕 的译文。
- SteamVR 菜单里有一页「甘岚翻译」：启停模式、改译文语言、手腕设置、翻译画面、界面语言。
  （桌面 / VR 只在主程序网页里切，附属程序本来就是给 VR 用的。）
- 数据由主程序（ui/app.py + vr_bridge.py）在 VR 模式下推过来；操作走主程序的 HTTP 接口。

运行：.venv\\Scripts\\pythonw.exe vr_overlay\\main.py [--manual]
  --manual：手动启动。SteamVR 没开时等它开，不会自己去拉起 SteamVR。
安装版是 GanlanTranslateVR.exe（入口 run_vr.py），设置、日志、manifest 放在 exe 旁边的 vr_overlay 文件夹。
"""

from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import logging
import math
import os
import subprocess
import sys
import time

FROZEN = getattr(sys, 'frozen', False)
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HERE = os.path.dirname(os.path.abspath(__file__))
if FROZEN:
    # 打包版：_internal 里不放可写文件，放到 exe 旁边
    DATA_DIR = os.path.join(os.path.dirname(sys.executable), 'vr_overlay')
    os.makedirs(DATA_DIR, exist_ok=True)
    os.chdir(os.path.dirname(sys.executable))
else:
    DATA_DIR = HERE
    if ROOT not in sys.path:
        sys.path.insert(0, ROOT)
    os.chdir(ROOT)

import numpy as np  # noqa: E402
import openvr  # noqa: E402

from vr_overlay import d3d_texture, render  # noqa: E402
from vr_overlay.link import AlreadyRunning, MainLink  # noqa: E402
from vr_overlay.settings import Settings  # noqa: E402

APP_KEY = 'ganlan.vroverlay'
MANIFEST_PATH = os.path.join(DATA_DIR, 'ganlan_vr.vrmanifest')
THUMB_PATH = os.path.join(DATA_DIR, 'thumbnail.png')
# 附属程序图标（岚给的绿色小人，2026-10-11）：SteamVR 菜单页缩略图 + SteamVR 应用列表。打包时随 vr_overlay 一起带上。
ICON_PATH = os.path.join(HERE, 'icon.png')
LOG_PATH = os.path.join(DATA_DIR, 'overlay.log')
FRAME_INTERVAL = 1 / 30
SCROLL_RESET_SECONDS = 30    # 翻过的手腕卡片多久没碰就回到最新
OTHERS_HISTORY_MAX = 40      # 附属程序自己攒的他人历史条数（主程序每次只推最近几条）
DASH_WIDTH_M = 2.6

log = logging.getLogger('vr_overlay')


# ---------------- 工具 ----------------

def setup_logging() -> None:
    handler = logging.FileHandler(LOG_PATH, encoding='utf-8')
    handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(message)s'))
    logging.basicConfig(level=logging.INFO, handlers=[handler])
    if sys.stdout is not None:
        logging.getLogger().addHandler(logging.StreamHandler(sys.stdout))

    def _excepthook(exc_type, exc, tb):
        log.error('未处理的异常', exc_info=(exc_type, exc, tb))
    sys.excepthook = _excepthook


def steamvr_running() -> bool:
    try:
        out = subprocess.run(['tasklist', '/FI', 'IMAGENAME eq vrserver.exe', '/NH'],
                             capture_output=True, text=True, timeout=5,
                             creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0)).stdout
        return 'vrserver.exe' in out.lower()
    except Exception:
        return False


def pythonw_path() -> str:
    exe = sys.executable
    candidate = os.path.join(os.path.dirname(exe), 'pythonw.exe')
    return candidate if os.path.exists(candidate) else exe


def write_manifest() -> None:
    manifest = {
        'source': 'builtin',
        'applications': [{
            'app_key': APP_KEY,
            'launch_type': 'binary',
            'binary_path_windows': sys.executable if FROZEN else pythonw_path(),
            'arguments': '' if FROZEN else f'"{os.path.abspath(__file__)}"',
            'is_dashboard_overlay': True,
            'image_path': ICON_PATH,
            'strings': {
                'en_us': {'name': 'Ganlan Translate VR', 'description': 'Wrist subtitles and settings for Ganlan Translate'},
                'zh_cn': {'name': '甘岚翻译 VR', 'description': '手腕字幕和设置面板'},
            },
        }],
    }
    with open(MANIFEST_PATH, 'w', encoding='utf-8') as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)


def forget_moved_install(apps) -> None:
    """装到了新位置（正式版起安装目录从测试版的 GanLan 改成 Ganlan Translate）时，SteamVR 里还登记着旧目录的
    manifest，同一个 app key 两份会冲突。登记的程序路径和自己不一样时，按旧路径推出旧 manifest 的位置注销掉。"""
    if not FROZEN:
        return
    try:
        if not apps.isApplicationInstalled(APP_KEY):
            return
        old_binary = apps.getApplicationPropertyString(APP_KEY, openvr.VRApplicationProperty_BinaryPath_String)
    except Exception:
        return
    if not old_binary or os.path.normcase(os.path.abspath(old_binary)) == os.path.normcase(sys.executable):
        return
    old_manifest = os.path.join(os.path.dirname(old_binary), 'vr_overlay', os.path.basename(MANIFEST_PATH))
    try:
        apps.removeApplicationManifest(old_manifest)
        log.info('已从 SteamVR 注销旧位置的 manifest：%s', old_manifest)
    except Exception as exc:
        log.warning('注销旧位置的 manifest 失败（%s）: %r', old_manifest, exc)


def register_with_steamvr() -> None:
    """注册 manifest 并开启「SteamVR 启动时自动启动」。每次启动都做一遍，路径变了也能更新。"""
    write_manifest()
    apps = openvr.VRApplications()
    forget_moved_install(apps)
    try:
        apps.addApplicationManifest(MANIFEST_PATH, False)
    except Exception as exc:
        log.warning('注册 manifest 失败: %s', exc)
        return
    try:
        apps.setApplicationAutoLaunch(APP_KEY, True)
        log.info('已注册到 SteamVR，自动启动：%s', apps.getApplicationAutoLaunch(APP_KEY))
    except Exception as exc:
        log.warning('设置自动启动失败: %s', exc)
    try:
        apps.identifyApplication(os.getpid(), APP_KEY)
    except Exception:
        pass


def to_hmd34(matrix: np.ndarray) -> openvr.HmdMatrix34_t:
    out = openvr.HmdMatrix34_t()
    for r in range(3):
        for c in range(4):
            out.m[r][c] = float(matrix[r, c])
    return out


def from_hmd34(m) -> np.ndarray:
    out = np.eye(4)
    for r in range(3):
        for c in range(4):
            out[r, c] = m.m[r][c]
    return out


def wrist_transform(side: str, s: Settings) -> np.ndarray:
    """手柄坐标系里手表的位置。

    手柄坐标：+X 右，+Y 上，-Z 朝前（指向手指方向），+Z 朝手腕。
    正常握持时左手背朝 -X，右手背朝 +X。卡片法线朝手背，文字的「右」：
    左手指向手指（-Z），右手指向手肘（+Z）——这样手心朝下抬腕看时，文字正着读。
    """
    sign = -1.0 if side == 'left' else 1.0
    normal = np.array([sign, 0.0, 0.0])
    right = np.array([0.0, 0.0, -1.0 if side == 'left' else 1.0])
    up = np.cross(normal, right)
    rot = np.column_stack([right, up, normal])
    # 绕手臂（Z 轴）再转
    a = math.radians(float(s.get('wrist_roll')) * -sign)
    rz = np.array([[math.cos(a), -math.sin(a), 0], [math.sin(a), math.cos(a), 0], [0, 0, 1]])
    rot = rz @ rot
    pos = np.array([sign * float(s.get('wrist_out')), float(s.get('wrist_up')), float(s.get('wrist_along'))])
    pos = rz @ pos
    m = np.eye(4)
    m[:3, :3] = rot
    m[:3, 3] = pos
    return m


# ---------------- 主体 ----------------

class OverlayApp:
    def __init__(self, link: MainLink, settings: Settings):
        self.link = link
        self.settings = settings
        self.overlay = openvr.VROverlay()
        self.system = openvr.VRSystem()
        self.wrists = {}
        self.wrist_device = {'left': None, 'right': None}
        self.wrist_alpha = {'left': 0.0, 'right': 0.0}
        self.wrist_target = {'left': 0.0, 'right': 0.0}   # 抬腕判断出来的目标透明度
        self.wrist_rendered = {'left': None, 'right': None}
        self.wrist_pose_version = -1
        self.dash = None
        self.dash_hits = []
        self.dash_hover = None
        self.dash_menu = None      # 正在展开的下拉菜单：('lang', mode) / ('ui_lang',)
        self.dash_rendered = None
        self.dash_visible = False
        self.poses = (openvr.TrackedDevicePose_t * openvr.k_unMaxTrackedDeviceCount)()
        self._last_device_check = 0.0
        self._last_wrist_render = 0.0
        self._uploaded = {}          # overlay -> 上次成功交给 SteamVR 的画面摘要
        self._upload_retry_at = {}   # overlay -> 失败后什么时候再试
        self._last_upload_warn = 0.0
        self._last_loop_error = 0.0
        # 手腕卡片的按钮：翻页、屏幕「识别」
        self.wrist_scroll = {'left': 0, 'right': 0}
        self.wrist_hits = {'left': [], 'right': []}
        self.wrist_hover = {'left': None, 'right': None}
        self._last_touch = {'left': 0.0, 'right': 0.0}
        self._wrist_interactive = {'left': None, 'right': None}   # 当前是否开着激光交互
        self.others_history = []          # 他人历史（按 ts 合并主程序推来的最近几条）
        self._history_version = -1
        self._vr_textures = {}       # overlay -> openvr.Texture_t（指向 self._d3d 里的贴图）
        self._d3d = self._init_d3d()
        self._create_overlays()

    def _init_d3d(self):
        """建 D3D11 贴图（见 d3d_texture），失败就退回 SetOverlayRaw。"""
        if self.settings.get('texture_upload') == 'raw':
            log.info('贴图方式：SetOverlayRaw（settings.json 里 texture_upload=raw）')
            return None
        try:
            adapter = self.system.getDXGIOutputInfo()
            d3d = d3d_texture.D3D11Textures(adapter)
        except Exception as exc:
            log.warning('D3D11 初始化失败，改用 SetOverlayRaw（换图时可能会闪）: %r', exc)
            return None
        log.info('贴图方式：D3D11，显卡 %s（%s）', adapter, d3d.adapter_name)
        return d3d

    def _create_overlays(self) -> None:
        ov = self.overlay
        for side in ('left', 'right'):
            handle = ov.createOverlay(f'{APP_KEY}.wrist.{side}', f'甘岚翻译 {side}')
            ov.setOverlayWidthInMeters(handle, float(self.settings.get('wrist_width')))
            ov.setOverlayAlpha(handle, 0.0)
            ov.setOverlaySortOrder(handle, 10)
            # 手腕卡片的按钮用 SteamVR 激光点（扳机），指着卡片推摇杆也能翻页
            ov.setOverlayInputMethod(handle, openvr.VROverlayInputMethod_Mouse)
            wrist_scale = openvr.HmdVector2_t()
            wrist_scale.v[0], wrist_scale.v[1] = render.WRIST_W, render.WRIST_H
            ov.setOverlayMouseScale(handle, wrist_scale)
            ov.setOverlayFlag(handle, openvr.VROverlayFlags_SendVRDiscreteScrollEvents, True)
            self.wrists[side] = handle
        main_handle, thumb = ov.createDashboardOverlay(f'{APP_KEY}.dashboard', '甘岚翻译')
        self.dash = main_handle
        ov.setOverlayWidthInMeters(main_handle, DASH_WIDTH_M)
        ov.setOverlayInputMethod(main_handle, openvr.VROverlayInputMethod_Mouse)
        scale = openvr.HmdVector2_t()
        scale.v[0], scale.v[1] = render.DASH_W, render.DASH_H
        ov.setOverlayMouseScale(main_handle, scale)
        try:
            if os.path.exists(ICON_PATH):
                ov.setOverlayFromFile(thumb, ICON_PATH)
            else:
                if not os.path.exists(THUMB_PATH):
                    render.render_thumbnail(THUMB_PATH)
                ov.setOverlayFromFile(thumb, THUMB_PATH)
        except Exception as exc:
            log.warning('缩略图设置失败: %s', exc)

    # ---------- 贴图 ----------

    def _upload(self, handle, img) -> bool:
        """把画面交给 SteamVR，返回 True 表示 SteamVR 那边已经是这张。

        优先用 D3D11 贴图 + SetOverlayTexture（d3d_texture.py）。退回 SetOverlayRaw 时要注意：
        - 它每次都让 SteamVR 新建一张贴图，换图时会空一帧。2026-10-11 真机上内存块问题修好后，
          菜单页（1280×800）在激光扫过按钮、重画高亮时还是闪。
        - 每次都占一块共享内存，SteamVR 取走才释放，最多 200 块（真机日志：「Refusing to create memory
          block because 201 blocks are already outstanding」）。所以内容没变就不传；隐藏的手腕卡片不传
          （SteamVR 不取，内存块一直占着）。
        - 失败了保留上一张、过一秒再试。不再退回 SetOverlayFromFile：它是异步加载，每次都空一帧（一直闪），
          写同一个 PNG 还会撞上 SteamVR 正在读而 PermissionError 崩溃。
        """
        data = img.tobytes()
        digest = hashlib.blake2b(data, digest_size=16).digest()
        if self._uploaded.get(handle) == digest:
            return True
        now = time.time()
        if now < self._upload_retry_at.get(handle, 0.0):
            return False
        if self._d3d is not None:
            try:
                tex = self._vr_textures.get(handle)
                if tex is None:
                    tex = openvr.Texture_t()
                    tex.eType = openvr.TextureType_DirectX
                    tex.eColorSpace = openvr.ColorSpace_Auto
                    self._vr_textures[handle] = tex
                tex.handle = self._d3d.update(handle, data, img.width, img.height)
                self.overlay.setOverlayTexture(handle, tex)
                # SteamVR 在 SetOverlayTexture 里把拷贝命令排进我们的上下文，不提交就一直是空贴图（见 d3d_texture.flush）
                self._d3d.flush()
            except Exception as exc:
                log.warning('D3D11 贴图失败，改用 SetOverlayRaw（换图时可能会闪）: %r', exc)
                self._close_d3d()
                # 其他 overlay 也要用 Raw 重画重传
                self._uploaded.clear()
                self.dash_rendered = None
                self.wrist_rendered = {'left': None, 'right': None}
            else:
                self._uploaded[handle] = digest
                return True
        buf = (ctypes.c_ubyte * len(data)).from_buffer_copy(data)
        try:
            self.overlay.setOverlayRaw(handle, buf, img.width, img.height, 4)
        except Exception as exc:
            self._upload_retry_at[handle] = now + 1.0
            self._uploaded.pop(handle, None)
            if now - self._last_upload_warn > 10:
                self._last_upload_warn = now
                log.warning('setOverlayRaw 失败（%dx%d），保留上一张画面，1 秒后再试: %r',
                            img.width, img.height, exc)
            return False
        self._uploaded[handle] = digest
        return True

    def _close_d3d(self) -> None:
        d3d, self._d3d = self._d3d, None
        self._vr_textures.clear()
        if d3d is not None:
            try:
                d3d.close()
            except Exception:
                pass

    def _link_state(self) -> dict:
        return {
            'connected': self.link.connected,
            'display_mode': self.link.display_mode,
            'busy': set(self.link.busy),
            'toast': self.link.current_toast(),
        }

    def render_dashboard(self, force=False) -> None:
        # 退回 SetOverlayRaw 时每次换图都会闪，激光扫过按钮就不画高亮了，只在点击、内容变化时重画
        hover = self.dash_hover if self._d3d is not None else None
        key = (self.link.version, self.settings.version, hover, self.dash_menu, self.link.connected,
               self.link.display_mode, bool(self.link.current_toast()))
        if not force and key == self.dash_rendered:
            return
        payload = self.link.snapshot()
        data = dict(self.settings.data)
        data['ui_language'] = self.ui_language(payload)
        img, self.dash_hits = render.render_dashboard(payload, self._link_state(), data, hover=hover,
                                                      open_menu=self.dash_menu)
        if self._upload(self.dash, img):
            self.dash_rendered = key

    def ui_language(self, payload: dict) -> str:
        """VR 菜单里选过就用选的；没选过跟主程序的界面语言，主程序没连上就用系统语言。"""
        chosen = self.settings.get('ui_language')
        if chosen:
            return chosen
        if payload.get('ui_language'):
            return payload['ui_language']
        import ui_locale
        return ui_locale.system_ui_language()

    def render_wrists(self) -> None:
        now = time.time()
        if now - self._last_wrist_render < 0.1:
            return
        self._last_wrist_render = now
        for side in self.wrists:
            if self.wrist_scroll[side] and now - self._last_touch[side] > SCROLL_RESET_SECONDS:
                self.wrist_scroll[side] = 0
        payload = self.link.snapshot()
        connected = self.link.connected
        for side in self.wrists:
            # 隐藏着的卡片不更新（见 _upload），要显示时 update_wrist_visibility 会先补画
            if self.wrist_alpha[side] <= 0 and self.wrist_target[side] <= 0:
                continue
            self._render_wrist(side, payload, connected)

    def update_history(self) -> None:
        """主程序每次只推最近几条他人字幕，这里按 ts 攒起来，手腕卡片才能往回翻。"""
        if self._history_version == self.link.version:
            return
        self._history_version = self.link.version
        lines = (self.link.snapshot().get('others') or {}).get('lines') or []
        if not lines:
            return
        if any(not line.get('ts') for line in lines):
            return   # 旧版主程序不带 ts，就只用它推来的几条
        index = {line.get('ts'): i for i, line in enumerate(self.others_history)}
        for line in lines:
            i = index.get(line.get('ts'))
            if i is None:
                self.others_history.append(dict(line))
            else:
                self.others_history[i] = dict(line)
        self.others_history.sort(key=lambda line: line.get('ts') or 0)
        del self.others_history[:-OTHERS_HISTORY_MAX]

    def _wrist_payload(self, payload: dict) -> dict:
        if not self.others_history or not isinstance(payload.get('others'), dict):
            return payload
        payload = dict(payload)
        others = dict(payload['others'])
        others['lines'] = list(self.others_history)
        payload['others'] = others
        return payload

    def _render_wrist(self, side: str, payload: dict, connected: bool) -> None:
        content = self.settings.get(f'{side}_wrist')
        if content == 'off':
            return
        payload = self._wrist_payload(payload)
        ui_lang = self.ui_language(payload)
        font_scale = float(self.settings.get('wrist_font_scale') or 1.0)
        limit = render.wrist_scroll_limit(content, payload)
        self.wrist_scroll[side] = max(0, min(self.wrist_scroll[side], limit))
        key = (content, self.link.version, connected, ui_lang, font_scale, self.wrist_scroll[side],
               self.wrist_hover[side], len(self.others_history))
        if key == self.wrist_rendered[side]:
            return
        img, hits = render.render_wrist(content, payload, connected, side, ui_lang, font_scale=font_scale,
                                        scroll=self.wrist_scroll[side], hover=self.wrist_hover[side])
        self.wrist_hits[side] = hits
        self._set_wrist_interactive(side, bool(hits))
        if self._upload(self.wrists[side], img):
            self.wrist_rendered[side] = key

    # ---------- 位置与显隐 ----------

    def update_wrist_devices(self) -> None:
        now = time.time()
        if now - self._last_device_check < 1.0 and self.wrist_pose_version == self.settings.version:
            return
        self._last_device_check = now
        roles = {'left': openvr.TrackedControllerRole_LeftHand, 'right': openvr.TrackedControllerRole_RightHand}
        for side, handle in self.wrists.items():
            index = self.system.getTrackedDeviceIndexForControllerRole(roles[side])
            if index == openvr.k_unTrackedDeviceIndexInvalid:
                index = None
            if index != self.wrist_device[side] or self.wrist_pose_version != self.settings.version:
                self.wrist_device[side] = index
                self.overlay.setOverlayWidthInMeters(handle, float(self.settings.get('wrist_width')))
                if index is not None:
                    self.overlay.setOverlayTransformTrackedDeviceRelative(
                        handle, index, to_hmd34(wrist_transform(side, self.settings)))
        self.wrist_pose_version = self.settings.version

    def update_wrist_visibility(self) -> None:
        show_any = self.link.vr_mode   # 主程序没开或在桌面模式时手腕不显示
        gaze_only = bool(self.settings.get('gaze_only'))
        max_alpha = float(self.settings.get('wrist_alpha'))
        need_poses = gaze_only and show_any
        if need_poses:
            self.system.getDeviceToAbsoluteTrackingPose(openvr.TrackingUniverseStanding, 0, self.poses)
        hmd = self.poses[openvr.k_unTrackedDeviceIndex_Hmd]
        for side, handle in self.wrists.items():
            index = self.wrist_device[side]
            target = 0.0
            if show_any and index is not None and self.settings.get(f'{side}_wrist') != 'off':
                if not gaze_only:
                    target = max_alpha
                else:
                    pose = self.poses[index]
                    if pose.bPoseIsValid and hmd.bPoseIsValid:
                        world = from_hmd34(pose.mDeviceToAbsoluteTracking) @ wrist_transform(side, self.settings)
                        center = world[:3, 3]
                        normal = world[:3, 2]
                        head = from_hmd34(hmd.mDeviceToAbsoluteTracking)[:3, 3]
                        to_head = head - center
                        dist = float(np.linalg.norm(to_head)) or 1.0
                        facing = float(np.dot(normal, to_head / dist))
                        # 卡片朝向眼睛（夹角 < ~55°）且离得不远才显示，边缘渐变
                        k = min(1.0, max(0.0, (facing - 0.45) / 0.25))
                        target = max_alpha * k if dist < 0.9 else 0.0
            self.wrist_target[side] = target
            current = self.wrist_alpha[side]
            if target > 0 and current == 0:
                # 隐藏期间没更新过画面，显示前先补一张最新的
                self._render_wrist(side, self.link.snapshot(), self.link.connected)
            new = current + (target - current) * 0.35
            if abs(new - target) < 0.02:
                new = target
            if abs(new - current) > 0.005 or (new == 0.0) != (current == 0.0):
                self.overlay.setOverlayAlpha(handle, new)
                if new > 0 and current == 0:
                    self.overlay.showOverlay(handle)
                elif new == 0:
                    self.overlay.hideOverlay(handle)
                self.wrist_alpha[side] = new

    # ---------- 手腕卡片的激光交互 ----------

    def _set_wrist_interactive(self, side: str, enabled: bool) -> None:
        """有按钮的卡片才开激光：MakeOverlaysInteractiveIfVisible 让卡片露出来时 SteamVR 激光能指它。
        自我卡片没按钮就不开，免得抬腕看字幕时激光挡着、扳机被它吃掉。"""
        if self._wrist_interactive[side] == enabled:
            return
        self._wrist_interactive[side] = enabled
        if not enabled:
            self.wrist_hover[side] = None
        try:
            self.overlay.setOverlayFlag(self.wrists[side],
                                        openvr.VROverlayFlags_MakeOverlaysInteractiveIfVisible, enabled)
        except Exception as exc:
            log.warning('手腕卡片激光交互设置失败: %r', exc)

    def _poll_wrist_events(self, event) -> None:
        for side, handle in self.wrists.items():
            while True:
                ok, event = self.overlay.pollNextOverlayEvent(handle, event)
                if not ok:
                    break
                et = event.eventType
                if et in (openvr.VREvent_MouseMove, openvr.VREvent_MouseButtonDown):
                    u = event.data.mouse.x
                    v = render.WRIST_H - event.data.mouse.y   # OpenVR 鼠标坐标原点在左下
                    hit = self._wrist_hit(side, u, v)
                    if et == openvr.VREvent_MouseMove:
                        self.wrist_hover[side] = hit
                    elif hit is not None:
                        self._last_touch[side] = time.time()
                        self.on_wrist_action(side, hit)
                elif et == openvr.VREvent_FocusLeave:
                    self.wrist_hover[side] = None
                elif et == openvr.VREvent_ScrollDiscrete:
                    dy = event.data.scroll.ydelta
                    if dy:
                        self._last_touch[side] = time.time()
                        self.on_wrist_action(side, ('wheel', 1 if dy > 0 else -1))

    def _wrist_hit(self, side, u, v):
        for x0, y0, x1, y1, action in self.wrist_hits[side]:
            if x0 - 8 <= u <= x1 + 8 and y0 - 8 <= v <= y1 + 8:
                return action
        return None

    def on_wrist_action(self, side: str, action) -> None:
        log.info('手腕点击 %s %s', side, action)
        if action[0] == 'scroll':
            self.wrist_scroll[side] = max(0, self.wrist_scroll[side] + int(action[1]))
        elif action[0] == 'wheel':
            # 摇杆往上推：他人看更早的，屏幕往上翻
            up = int(action[1]) > 0
            content = self.settings.get(f'{side}_wrist')
            delta = (1 if up else -1) if content == 'others' else (-1 if up else 1)
            self.wrist_scroll[side] = max(0, self.wrist_scroll[side] + delta)
        elif action[0] == 'screen_translate':
            self.link.screen_translate()

    # ---------- 事件 ----------

    def poll_events(self) -> bool:
        """返回 False 表示 SteamVR 要我们退出。"""
        event = openvr.VREvent_t()
        while self.system.pollNextEvent(event):
            if event.eventType == openvr.VREvent_Quit:
                log.info('SteamVR 要求退出')
                try:
                    self.system.acknowledgeQuit_Exiting()
                except Exception:
                    pass
                return False
        while True:
            ok, event = self.overlay.pollNextOverlayEvent(self.dash, event)
            if not ok:
                break
            et = event.eventType
            if et in (openvr.VREvent_MouseMove, openvr.VREvent_MouseButtonDown):
                x = event.data.mouse.x
                y = render.DASH_H - event.data.mouse.y   # OpenVR 鼠标坐标原点在左下
                hit = self._hit(x, y)
                if et == openvr.VREvent_MouseMove:
                    self.dash_hover = hit
                elif hit is not None:
                    self.on_action(hit)
            elif et == openvr.VREvent_OverlayShown:
                self.dash_visible = True
                self.render_dashboard(force=True)
            elif et == openvr.VREvent_OverlayHidden:
                self.dash_visible = False
                self.dash_hover = None
                self.dash_menu = None
        self._poll_wrist_events(event)
        return True

    def _hit(self, x, y):
        for x0, y0, x1, y1, action in self.dash_hits:
            if x0 <= x <= x1 and y0 <= y <= y1:
                return action
        return None

    def on_action(self, action) -> None:
        log.info('点击 %s', action)
        kind = action[0]
        link = self.link
        if kind == 'menu':
            self.dash_menu = None if self.dash_menu == action[1] else action[1]
            return
        # 其他点击（选了语言、点了菜单外面）都会收起菜单
        self.dash_menu = None
        if kind == 'close_menu':
            return
        if kind == 'mode':
            _, mode, op = action
            (link.start_modes if op == 'start' else link.stop_modes)([mode])
        elif kind == 'mode_all':
            modes = ['self', 'others', 'screen']
            (link.start_modes if action[1] == 'start' else link.stop_modes)(modes)
        elif kind == 'lang':
            link.set_language(action[1], action[2])
        elif kind == 'ui_lang':
            self.settings.set('ui_language', action[1])
        elif kind == 'wrist':
            self.settings.set(f'{action[1]}_wrist', action[2])
            self.wrist_rendered[action[1]] = None
        elif kind == 'toggle':
            self.settings.set(action[1], not self.settings.get(action[1]))
        elif kind == 'adjust':
            self.settings.adjust(action[1], action[2])
        elif kind == 'reset_wrist':
            self.settings.reset_wrist_pose()
        elif kind == 'screen_translate':
            link.screen_translate()

    # ---------- 主循环 ----------

    def run(self) -> None:
        log.info('VR 附属程序开始运行')
        self.render_dashboard(force=True)
        while True:
            started = time.time()
            if not self.poll_events():
                break
            try:
                self.update_wrist_devices()
                self.update_history()
                self.render_wrists()
                self.update_wrist_visibility()
                if self.dash_visible or self.overlay.isDashboardVisible():
                    self.render_dashboard()
            except Exception:
                # 某一帧出错就跳过，别让整个附属程序崩掉
                if started - self._last_loop_error > 10:
                    self._last_loop_error = started
                    log.exception('主循环出错，已跳过这一帧')
            time.sleep(max(0.0, FRAME_INTERVAL - (time.time() - started)))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--manual', action='store_true', help='手动启动：SteamVR 没开就等它开')
    parser.add_argument('--main-url', default='http://127.0.0.1:5001')
    parser.add_argument('--udp-port', type=int, default=5012)
    args = parser.parse_args(argv)

    setup_logging()
    try:
        link = MainLink(args.main_url, args.udp_port)
    except AlreadyRunning:
        log.info('已经有一个 VR 附属程序在运行，退出')
        return 0
    link.start()

    if args.manual:
        waited = False
        while not steamvr_running():
            if not waited:
                log.info('等待 SteamVR 启动…')
                waited = True
            time.sleep(3)
        if waited:
            time.sleep(5)   # 刚起来的 SteamVR 给它一点时间

    try:
        openvr.init(openvr.VRApplication_Overlay)
    except Exception as exc:
        log.error('连接 SteamVR 失败: %s', exc)
        link.stop()
        return 1
    try:
        register_with_steamvr()
        OverlayApp(link, Settings()).run()
    finally:
        link.stop()
        try:
            openvr.shutdown()
        except Exception:
            pass
        log.info('VR 附属程序已退出')
    return 0


def run_and_exit(argv=None) -> None:
    """跑 main，然后无论如何都结束进程。

    2026-10-11 真机：崩溃后进程没退干净，挂在后台，再启动就有两个 GanLanVR.exe（现在叫 GanlanTranslateVR.exe）。
    """
    code = 1
    try:
        code = main(argv)
    except BaseException:
        log.exception('VR 附属程序异常退出')
    finally:
        logging.shutdown()
        os._exit(code or 0)


if __name__ == '__main__':
    run_and_exit()
