"""用 PIL 画手腕卡片和 SteamVR 菜单页。配色照主程序网页（style.css 的令牌）。

手腕卡片只显示，返回 RGBA 图。菜单页返回 (RGBA 图, 可点区域列表)，可点区域是 (x0, y0, x1, y1, action)，
action 是元组，交给 main.py 处理。
"""

from __future__ import annotations

import os
from functools import lru_cache

from PIL import Image, ImageDraw, ImageFont

from vr_overlay.strings import normalize, translator

# ---- 主程序的颜色令牌 ----
BG = (233, 240, 234, 255)          # panel --bg #E9F0EA
SURFACE = (255, 255, 255, 255)
SURFACE_SOFT = (245, 248, 244, 255)
GREEN_50 = (228, 242, 232, 255)
GREEN_100 = (207, 230, 215, 255)
GREEN_400 = (95, 192, 141, 255)
GREEN_600 = (31, 122, 77, 255)
GREEN_700 = (23, 100, 62, 255)
PRIMARY = (30, 86, 61, 255)
INK = (18, 38, 26, 255)
INK_2 = (63, 91, 73, 255)
INK_3 = (124, 146, 133, 255)
INK_4 = (154, 175, 161, 255)
LINE = (227, 234, 225, 255)
LINE_STRONG = (203, 217, 199, 255)
WARN = (180, 83, 9, 255)
DANGER = (196, 70, 58, 255)
DANGER_BG = (252, 237, 235, 255)

LANGUAGES = [('zh-CN', '简体中文'), ('zh-TW', '繁體中文'), ('en', 'English'), ('ja', '日本語'),
             ('ko', '한국어'), ('ru', 'Русский'), ('es', 'Español'), ('fr', 'Français'),
             ('de', 'Deutsch'), ('pt', 'Português'), ('it', 'Italiano'), ('id', 'Indonesia')]
UI_LANGUAGE_NAMES = {'zh-CN': '中文', 'en': 'English', 'ja': '日本語', 'ko': '한국어'}

FONT_DIR = os.path.join(os.environ.get('WINDIR', r'C:\Windows'), 'Fonts')
ICON_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'icon.png')   # 附属程序图标（绿色小人）


@lru_cache(maxsize=4)
def _logo(size: int):
    """菜单页左上角的图标：icon.png 裁成圆角方块；没有文件就返回 None（退回画「译」字）。"""
    try:
        icon = Image.open(ICON_PATH).convert('RGBA').resize((size, size), Image.LANCZOS)
    except Exception:
        return None
    mask = Image.new('L', (size, size), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, size - 1, size - 1), radius=size // 4, fill=255)
    icon.putalpha(mask)
    return icon


# ---------------- 字体与排版 ----------------

@lru_cache(maxsize=64)
def _font(kind: str, size: int):
    names = {
        'cjk': ['msyh.ttc', 'msyh.ttf', 'simhei.ttf'],
        'cjk_bold': ['msyhbd.ttc', 'msyh.ttc'],
        'hangul': ['malgun.ttf', 'msyh.ttc'],
        'hangul_bold': ['malgunbd.ttf', 'malgun.ttf'],
    }[kind]
    for name in names:
        path = os.path.join(FONT_DIR, name)
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size, index=0) if path.endswith('.ttc') else ImageFont.truetype(path, size)
            except Exception:
                continue
    return ImageFont.load_default()


def _is_hangul(ch: str) -> bool:
    code = ord(ch)
    return 0xAC00 <= code <= 0xD7A3 or 0x1100 <= code <= 0x11FF or 0x3130 <= code <= 0x318F


def _font_for(ch: str, size: int, bold: bool):
    if _is_hangul(ch):
        return _font('hangul_bold' if bold else 'hangul', size)
    return _font('cjk_bold' if bold else 'cjk', size)


def _runs(text: str, size: int, bold: bool):
    """按字体切段：韩文用 Malgun，其他用微软雅黑。"""
    runs = []
    for ch in text:
        font = _font_for(ch, size, bold)
        if runs and runs[-1][1] is font:
            runs[-1][0] += ch
        else:
            runs.append([ch, font])
    return runs


@lru_cache(maxsize=8192)
def _char_width(ch: str, size: int, bold: bool) -> float:
    return _font_for(ch, size, bold).getlength(ch)


# 菜单页的字整体缩小用；render_dashboard 里临时设成 DASH_FONT_SCALE
_font_scale = 1.0


def _px(size: int) -> int:
    return max(8, int(round(size * _font_scale)))


def text_width(text: str, size: int, bold: bool = False) -> float:
    px = _px(size)
    return sum(_char_width(ch, px, bold) for ch in text)


def draw_text(draw: ImageDraw.ImageDraw, xy, text: str, size: int, fill, bold: bool = False):
    x, y = xy
    px = _px(size)
    y += (size - px) * 0.69   # 缩小后的字放在原行框中间
    for chunk, font in _runs(text, px, bold):
        draw.text((x, y), chunk, font=font, fill=fill)
        x += font.getlength(chunk)
    return x


def _tokens(text: str):
    """拆成可换行的片段：中日文每个字一段，拉丁单词、韩文词（韩文按空格换行）连同后面的空格一段。"""
    token = ''
    for ch in text:
        if ch == '\n':
            if token:
                yield token
                token = ''
            yield '\n'
        elif ord(ch) > 0x2E80 and not _is_hangul(ch):   # 中日
            if token:
                yield token
                token = ''
            yield ch
        elif ch == ' ':
            yield token + ' '
            token = ''
        else:
            token += ch
    if token:
        yield token


def wrap(text: str, size: int, max_width: float, bold: bool = False, max_lines: int = 0):
    lines, line, width = [], '', 0.0
    for token in _tokens(text or ''):
        if token == '\n':
            lines.append(line)
            line, width = '', 0.0
            continue
        w = text_width(token, size, bold)
        if line and width + w > max_width:
            lines.append(line.rstrip())
            line, width = '', 0.0
            token = token.lstrip()
            w = text_width(token, size, bold)
        while w > max_width and len(token) > 1:   # 超长单词硬切
            cut = len(token)
            while cut > 1 and text_width(token[:cut], size, bold) > max_width:
                cut -= 1
            lines.append(token[:cut])
            token = token[cut:]
            w = text_width(token, size, bold)
        line += token
        width += w
    if line or not lines:
        lines.append(line.rstrip())
    if max_lines and len(lines) > max_lines:
        lines = lines[:max_lines]
        last = lines[-1]
        while last and text_width(last + '…', size, bold) > max_width:
            last = last[:-1]
        lines[-1] = last + '…'
    return lines


def line_height(size: int) -> int:
    return int(size * 1.38)


# ---------------- 小部件 ----------------

def rounded(draw, box, radius, fill=None, outline=None, width=1):
    draw.rounded_rectangle(box, radius=radius, fill=fill, outline=outline, width=width)


def pill(draw, box, label, size, active=False, hover=False, busy=False, danger=False, primary=False,
         radius=None):
    x0, y0, x1, y1 = box
    if primary:
        fill, fg, outline = (GREEN_700 if hover else GREEN_600), SURFACE, None
    elif danger:
        fill, fg, outline = ((248, 222, 218, 255) if hover else DANGER_BG), DANGER, (240, 200, 195, 255)
    elif active:
        fill, fg, outline = GREEN_600, SURFACE, None
    else:
        fill, fg, outline = (GREEN_50 if hover else SURFACE), INK_2, LINE_STRONG
    rounded(draw, box, (y1 - y0) // 2 if radius is None else radius, fill=fill, outline=outline, width=2)
    text = '…' if busy else label
    tw = text_width(text, size, bold=True)
    draw_text(draw, (x0 + (x1 - x0 - tw) / 2, y0 + (y1 - y0 - line_height(size)) / 2 + 1), text, size, fg,
              bold=True)


def dropdown(draw, box, label, size, is_open=False, hover=False):
    """点开会弹出菜单的按钮：左边是当前值，右边一个小箭头。"""
    x0, y0, x1, y1 = box
    rounded(draw, box, (y1 - y0) // 2, fill=GREEN_50 if (hover or is_open) else SURFACE,
            outline=GREEN_600 if is_open else LINE_STRONG, width=2)
    draw_text(draw, (x0 + 18, y0 + (y1 - y0 - line_height(size)) / 2 + 1),
              fit(label, size, x1 - x0 - 58, True), size, INK, bold=True)
    cx, cy = x1 - 24, (y0 + y1) / 2
    dy = -4 if is_open else 4
    draw.line([(cx - 7, cy - dy / 1.0), (cx, cy + dy), (cx + 7, cy - dy / 1.0)], fill=INK_2, width=3,
              joint='curve')


def arrow_button(draw, box, up, enabled=True, hover=False):
    """▲ / ▼ 按钮，不能按时箭头变灰。"""
    x0, y0, x1, y1 = box
    rounded(draw, box, (y1 - y0) // 2, fill=GREEN_50 if hover else SURFACE,
            outline=GREEN_600 if hover else LINE_STRONG, width=2)
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    s = 10
    pts = [(cx - s, cy + s * 0.55), (cx + s, cy + s * 0.55), (cx, cy - s * 0.75)] if up else \
          [(cx - s, cy - s * 0.55), (cx + s, cy - s * 0.55), (cx, cy + s * 0.75)]
    draw.polygon(pts, fill=INK_2 if enabled else LINE_STRONG)


def status_dot(draw, center, radius, color):
    cx, cy = center
    draw.ellipse((cx - radius, cy - radius, cx + radius, cy + radius), fill=color)


def mode_state(modes: dict, mode: str, t):
    """返回 (类别, 显示文字)。类别: running / starting / stopping / error / stopped"""
    raw = str((modes or {}).get(mode, 'stopped'))
    if raw.startswith('error'):
        return 'error', raw.split(':', 1)[-1].strip() or t('state.error')
    kind = raw if raw in ('running', 'starting', 'stopping') else 'stopped'
    return kind, t(f'state.{kind}')


def state_color(kind: str):
    return {'running': GREEN_400, 'starting': WARN, 'stopping': WARN, 'error': DANGER}.get(kind, INK_4)


def lang_label(code: str) -> str:
    for c, name in LANGUAGES:
        if c == code:
            return name
    return code or '?'


def fit(text: str, size: int, max_width: float, bold: bool = False) -> str:
    return wrap(text, size, max_width, bold=bold, max_lines=1)[0]


# ---------------- 手腕卡片 ----------------

WRIST_W, WRIST_H = 640, 400
WRIST_PAD = 30
WRIST_BODY_TOP, WRIST_BODY_BOTTOM = 100, WRIST_H - 26


def wrist_scroll_limit(content: str, payload: dict) -> int:
    """能往回翻几条。他人：从最新往旧翻；屏幕：从上往下翻。"""
    section = payload.get(content) or {}
    if content == 'others':
        return max(0, len(section.get('lines') or []) - 1)
    if content == 'screen':
        return max(0, len(section.get('items') or []) - 1)
    return 0


def page_position(content: str, payload: dict, scroll: int):
    """(第几条, 共几条)。他人是正在看的最新一条，屏幕是正在看的第一段。"""
    section = payload.get(content) or {}
    if content == 'others':
        total = len(section.get('lines') or [])
        return max(0, total - scroll), total
    if content == 'screen':
        total = len(section.get('items') or [])
        return (min(scroll + 1, total) if total else 0), total
    return 0, 0


def render_wrist(content: str, payload: dict, connected: bool, side: str, ui_lang: str = 'zh-CN',
                 font_scale: float = 1.0, scroll: int = 0):
    """手腕卡片，只显示（不能点，见 main.OverlayApp._create_overlays）。

    font_scale：正文字号倍数；scroll：他人往旧翻了几条 / 屏幕往下翻了几条（在菜单页里翻）。
    """
    t = translator(ui_lang)
    img = Image.new('RGBA', (WRIST_W, WRIST_H), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    rounded(d, (2, 2, WRIST_W - 3, WRIST_H - 3), 40, fill=(250, 252, 249, 240), outline=LINE_STRONG, width=3)
    pad = WRIST_PAD
    fs = max(0.5, float(font_scale or 1.0))

    def sz(n):
        return max(10, int(round(n * fs)))

    modes = payload.get('modes') or {}
    kind, label = mode_state(modes, content, t)
    scroll = max(0, min(int(scroll or 0), wrist_scroll_limit(content, payload)))
    # 顶栏：状态点 + 模式名 …… 状态（翻过页时是「第几条/共几条」） + 译文语言
    status_dot(d, (pad + 10, 46), 8, state_color(kind) if connected else INK_4)
    draw_text(d, (pad + 30, 28), t(f'mode.{content}'), 25, INK, bold=True)
    section = payload.get(content) or {}
    target = section.get('target_language') or ''
    right = WRIST_W - pad
    if target:
        chip = '→ ' + lang_label(target)
        cw = text_width(chip, 18, bold=True) + 26
        rounded(d, (right - cw, 29, right, 63), 17, fill=GREEN_50)
        draw_text(d, (right - cw + 13, 33), chip, 18, PRIMARY, bold=True)
        right -= cw + 12
    state_text = t('state.error') if kind == 'error' else (label if connected else t('conn.no'))
    state_fill = DANGER if kind == 'error' else INK_3
    if scroll and connected and kind == 'running':
        pos, total = page_position(content, payload, scroll)
        state_text, state_fill = f'{pos}/{total}', PRIMARY
    sw = text_width(state_text, 18, bold=state_fill is PRIMARY)
    draw_text(d, (right - sw, 34), state_text, 18, state_fill, bold=state_fill is PRIMARY)
    d.line((pad, 84, WRIST_W - pad, 84), fill=LINE, width=2)

    body_top, body_bottom = WRIST_BODY_TOP, WRIST_BODY_BOTTOM
    width = WRIST_W - pad * 2

    def message(text, color=INK_3):
        y = body_top + 20
        for ln in wrap(text, sz(23), width, max_lines=max(2, int(6 / fs))):
            draw_text(d, (pad, y), ln, sz(23), color)
            y += line_height(sz(23))

    if not connected:
        message(t('wrist.notConnected'))
    elif kind == 'error':
        message(label, DANGER)
    elif kind != 'running':
        message(t('wrist.notStarted', mode=t(f'mode.{content}')))
    elif content == 'self':
        _wrist_self(d, section, pad, body_top, body_bottom, width, t, fs)
    elif content == 'others':
        lines = list(section.get('lines') or [])
        if scroll:
            lines = lines[:len(lines) - scroll]
        _wrist_entries(d, [
            {'badge': it.get('speaker', '?'), 'original': it.get('original', ''),
             'lang': it.get('lang', ''), 'translated': it.get('translated', '')}
            for it in lines
        ], pad, body_top, body_bottom, width, empty=t('wrist.othersEmpty'),
            ongoing=bool(section.get('ongoing')) and not scroll, fs=fs)
    elif content == 'screen':
        status = section.get('status', 'idle')
        items = [{'badge': '', 'original': it.get('original', ''), 'lang': it.get('lang', ''),
                  'translated': it.get('translated', '')} for it in (section.get('items') or [])]
        if section.get('busy') or status in ('capturing', 'recognizing', 'translating'):
            message(section.get('message') or t('wrist.screenBusy'), PRIMARY)
        elif status == 'error':
            message(section.get('message') or t('wrist.screenError'), DANGER)
        else:
            # 画面翻译从上往下显示（和截图里的阅读顺序一致）
            _wrist_entries(d, items[scroll:], pad, body_top, body_bottom, width, empty=t('wrist.screenEmpty'),
                           newest_last=False, fs=fs)
    return img


def _wrist_self(d, section, pad, top, bottom, width, t, fs=1.0):
    def sz(n):
        return max(10, int(round(n * fs)))

    original = (section.get('original') or '').strip()
    translated = (section.get('translated') or '').strip()
    if section.get('mic_open') is False and not original:
        y = top + 20
        for ln in wrap(t('wrist.micOff'), sz(23), width):
            draw_text(d, (pad, y), ln, sz(23), INK_3)
            y += line_height(sz(23))
        return
    if not original and not translated:
        draw_text(d, (pad, top + 20), fit(t('wrist.selfEmpty'), sz(23), width), sz(23), INK_4)
        return
    y = top + 6
    for ln in wrap(original + (' …' if section.get('ongoing') else ''), sz(19), width, max_lines=3):
        draw_text(d, (pad, y), ln, sz(19), INK_3)
        y += line_height(sz(19))
    y += 8
    remaining = max(1, int((bottom - y) // line_height(sz(26))))
    for ln in wrap(translated, sz(26), width, bold=True, max_lines=remaining):
        draw_text(d, (pad, y), ln, sz(26), INK, bold=True)
        y += line_height(sz(26))


def _layout_entry(entry, width, small=22, big=30, gap=14):
    badge_w = int(big * 1.45) if entry.get('badge') else 0
    tag = f"  [{entry['lang']}]" if entry.get('lang') else ''
    orig = wrap((entry.get('original') or '') + tag, small, width - badge_w, max_lines=2)
    trans = wrap(entry.get('translated') or '…', big, width - badge_w, bold=True, max_lines=3)
    height = len(orig) * line_height(small) + 4 + len(trans) * line_height(big) + gap
    return orig, trans, badge_w, height, small, big


def _draw_entry(d, entry, layout, x, y):
    orig, trans, badge_w, _h, small, big = layout
    if badge_w:
        r = int(small * 0.78)
        cy = y + line_height(small) / 2
        status_dot(d, (x + r, cy), r, GREEN_100)
        b = str(entry['badge'])[:1]
        draw_text(d, (x + r - text_width(b, small, True) / 2, cy - line_height(small) / 2), b, small, PRIMARY,
                  bold=True)
    tx = x + badge_w
    for ln in orig:
        draw_text(d, (tx, y), ln, small, INK_3)
        y += line_height(small)
    y += 4
    for ln in trans:
        draw_text(d, (tx, y), ln, big, INK, bold=True)
        y += line_height(big)


def _pick_entries(entries, avail, width, newest_last, small=22, big=30, gap=14):
    chosen, used = [], 0
    ordered = reversed(entries) if newest_last else entries
    for entry in ordered:
        layout = _layout_entry(entry, width, small, big, gap)
        if chosen and used + layout[3] > avail:
            break
        if newest_last:
            chosen.insert(0, (entry, layout))
        else:
            chosen.append((entry, layout))
        used += layout[3]
    return chosen


def _wrist_entries(d, entries, pad, top, bottom, width, empty, ongoing=False, newest_last=True, fs=1.0):
    def sz(n):
        return max(10, int(round(n * fs)))

    if not entries:
        y = top + 20
        for ln in wrap(empty, sz(23), width, max_lines=3):
            draw_text(d, (pad, y), ln, sz(23), INK_4)
            y += line_height(sz(23))
        return
    y = top + 4
    for entry, layout in _pick_entries(entries, bottom - top, width, newest_last, small=sz(18), big=sz(24),
                                       gap=sz(12)):
        _draw_entry(d, entry, layout, pad, y)
        y += layout[3]
    if ongoing:
        draw_text(d, (pad + width - 36, bottom - 28), '…', 23, INK_3, bold=True)


# ---------------- SteamVR 菜单页 ----------------
#
#  ┌ 顶栏：标志 + 标题        提示（没连上 / 桌面模式 / 操作失败）     连接状态  界面语言 ┐
#  ├ 翻译模式（左，每个模式一块：名字 状态 启停          │ 手腕显示（右上）          │
#  │   + 译成哪种语言；他人 / 屏幕还有 ▲ 第几条/共几条 ▼）│                          │
#  │                                               │ 画面翻译（右下，带结果）   │
#  └───────────────────────────────────────────────┴──────────────────────────┘

DASH_W, DASH_H = 1280, 800
COL_L = (32, 700)
COL_R = (724, DASH_W - 32)
TOP = 100
BOTTOM = DASH_H - 24


class _Hits:
    def __init__(self, hover):
        self.items = []
        self.hover = hover
        self.anchors = {}   # 下拉按钮的位置，弹出菜单靠它定位

    def add(self, box, action):
        self.items.append((*box, action))
        return self.hover == action


def _card(d, col, top, bottom, title, subtitle, reserve=48):
    """reserve：标题行右边留给按钮的宽度。"""
    x0, x1 = col
    rounded(d, (x0, top, x1, bottom), 20, fill=SURFACE, outline=LINE, width=2)
    draw_text(d, (x0 + 24, top + 16), title, 26, INK, bold=True)
    tw = text_width(title, 26, True)
    draw_text(d, (x0 + 36 + tw, top + 24), fit(subtitle, 16, x1 - x0 - 36 - tw - reserve), 16, INK_3)
    return top + 70


DASH_FONT_SCALE = 0.86


def render_dashboard(payload: dict, link_state: dict, settings: dict, hover=None, open_menu=None, scroll=None,
                     page='main', adjust_group=0):
    """scroll：{'others': n, 'screen': n}，菜单页里翻到哪了（手腕卡片显示同一个位置）。
    page：右边一栏显示什么，'main' = 手腕显示 + 画面翻译，'wrist' = 调整手腕卡片的位置和角度；
    adjust_group：调整页翻到第几组（WRIST_ADJUST_GROUPS）。"""
    global _font_scale
    _font_scale = DASH_FONT_SCALE
    try:
        return _render_dashboard(payload, link_state, settings, hover, open_menu, scroll or {}, page, adjust_group)
    finally:
        _font_scale = 1.0


def _render_dashboard(payload: dict, link_state: dict, settings: dict, hover, open_menu, scroll, page, adjust_group):
    ui_lang = normalize(settings.get('ui_language') or 'zh-CN')
    t = translator(ui_lang)
    img = Image.new('RGBA', (DASH_W, DASH_H), BG)
    d = ImageDraw.Draw(img)
    hits = _Hits(hover)
    connected = link_state.get('connected', False)
    busy = link_state.get('busy') or set()
    modes = payload.get('modes') or {}

    _header(d, hits, t, ui_lang, connected, link_state, open_menu)
    logo = _logo(52)
    if logo is not None:
        img.alpha_composite(logo, (32, 24))
    _modes_card(d, hits, t, payload, modes, busy, open_menu, scroll)
    if page == 'wrist':
        _wrist_adjust_card(d, hits, t, settings, adjust_group)
    else:
        wrist_bottom = _wrist_card(d, hits, t, settings)
        _screen_card(d, hits, t, payload, modes, busy, wrist_bottom + 16, scroll.get('screen', 0))
    if open_menu and open_menu in hits.anchors:
        img, menu_hits = _popup(img, hover, open_menu, hits.anchors[open_menu], payload, ui_lang)
        # 菜单项优先；点菜单外面任何地方只关菜单
        return img, menu_hits + [(0, 0, DASH_W, DASH_H, ('close_menu',))] + hits.items
    return img, hits.items


def _popup(img, hover, menu, anchor, payload, ui_lang):
    """在 anchor 下方（放不下就上方）弹出语言菜单。"""
    if menu[0] == 'lang':
        mode = menu[1]
        options = LANGUAGES
        current = (payload.get(mode) or {}).get('target_language') or ''
        cols, cell_w = 3, 158
        action = lambda code: ('lang', mode, code)  # noqa: E731
    else:
        options = list(UI_LANGUAGE_NAMES.items())
        current = ui_lang
        cols, cell_w = 1, 168
        action = lambda code: ('ui_lang', code)  # noqa: E731
    cell_h, gap, pad = 44, 8, 14
    rows = (len(options) + cols - 1) // cols
    w = pad * 2 + cols * cell_w + (cols - 1) * gap
    h = pad * 2 + rows * cell_h + (rows - 1) * gap
    anchor_box, anchor_label, anchor_size = anchor
    ax0, ay0, ax1, ay1 = anchor_box
    x0 = ax0 if menu[0] == 'lang' else ax1 - w
    x0 = max(16, min(x0, DASH_W - 16 - w))
    y0 = ay1 + 8
    if y0 + h > DASH_H - 16:
        y0 = ay0 - 8 - h

    # 背景压暗一点 + 菜单阴影
    layer = Image.new('RGBA', img.size, (0, 0, 0, 0))
    ld = ImageDraw.Draw(layer)
    ld.rectangle((0, 0, DASH_W, DASH_H), fill=(18, 38, 26, 46))
    ld.rounded_rectangle((x0 + 2, y0 + 8, x0 + w + 2, y0 + h + 10), radius=18, fill=(18, 38, 26, 50))
    img = Image.alpha_composite(img, layer)
    d = ImageDraw.Draw(img)
    rounded(d, (x0, y0, x0 + w, y0 + h), 18, fill=SURFACE, outline=LINE_STRONG, width=2)
    dropdown(d, anchor_box, anchor_label, anchor_size, is_open=True)   # 按钮本身不压暗

    menu_hits = []
    for i, (code, name) in enumerate(options):
        cx = x0 + pad + (i % cols) * (cell_w + gap)
        cy = y0 + pad + (i // cols) * (cell_h + gap)
        box = (cx, cy, cx + cell_w, cy + cell_h)
        act = action(code)
        menu_hits.append((*box, act))
        pill(d, box, name, 17, active=current == code, hover=hover == act, radius=12)
    return img, menu_hits


def _header(d, hits, t, ui_lang, connected, link_state, open_menu=None):
    if _logo(52) is None:
        rounded(d, (32, 24, 84, 76), 14, fill=GREEN_600)
        draw_text(d, (44, 28), '译', 30, SURFACE, bold=True)
    draw_text(d, (100, 20), t('app.name'), 32, INK, bold=True)
    draw_text(d, (102, 62), t('app.subtitle'), 16, INK_3)

    # 右：界面语言（点开选）
    box = (DASH_W - 32 - 168, 30, DASH_W - 32, 70)
    key = ('ui_lang',)
    dropdown(d, box, UI_LANGUAGE_NAMES.get(ui_lang, ui_lang), 17, is_open=open_menu == key,
             hover=hits.add(box, ('menu', key)))
    hits.anchors[key] = (box, UI_LANGUAGE_NAMES.get(ui_lang, ui_lang), 17)
    sx = box[0]
    # 右：连接状态
    conn = t('conn.ok') if connected else t('conn.no')
    cw = text_width(conn, 18, True) + 50
    cx1 = sx - 14
    rounded(d, (cx1 - cw, 30, cx1, 70), 20, fill=SURFACE, outline=LINE, width=2)
    status_dot(d, (cx1 - cw + 22, 50), 7, GREEN_400 if connected else DANGER)
    draw_text(d, (cx1 - cw + 36, 37), conn, 18, INK_2, bold=True)

    # 中：提示（不挤占下面的布局）
    notice = link_state.get('toast') or ''
    if notice.startswith('i18n:'):
        notice = t(notice[5:])
    if not notice and not connected:
        notice = t('notice.disconnected')
    elif not notice and link_state.get('display_mode') != 'vr':
        notice = t('notice.desktop')
    if notice:
        nx0, nx1 = 300, cx1 - cw - 16
        lines = wrap(notice, 16, nx1 - nx0 - 28, max_lines=2)
        h = len(lines) * line_height(16) + 14
        ny0 = 50 - h / 2
        rounded(d, (nx0, ny0, nx1, ny0 + h), 12, fill=(253, 244, 231, 255), outline=(240, 214, 180, 255))
        y = ny0 + 7
        for ln in lines:
            draw_text(d, (nx0 + 14, y), ln, 16, WARN)
            y += line_height(16)


def _modes_card(d, hits, t, payload, modes, busy, open_menu=None, scroll=None):
    x0, x1 = COL_L
    y = _card(d, COL_L, TOP, BOTTOM, t('card.modes'), t('card.modes.sub'), reserve=310)
    # 右上角：全部启动 / 全部停止
    bw = 132
    box = (x1 - 24 - bw, TOP + 14, x1 - 24, TOP + 56)
    pill(d, box, t('btn.stopAll'), 18, hover=hits.add(box, ('mode_all', 'stop')))
    box = (x1 - 36 - bw * 2, TOP + 14, x1 - 36 - bw, TOP + 56)
    pill(d, box, t('btn.startAll'), 18, primary=True, hover=hits.add(box, ('mode_all', 'start')))

    block_h, gap = 186, 14
    for mode in ('self', 'others', 'screen'):
        kind, label = mode_state(modes, mode, t)
        running = kind in ('running', 'starting')
        bx0, bx1 = x0 + 20, x1 - 20
        rounded(d, (bx0, y, bx1, y + block_h), 16,
                fill=GREEN_50 if kind == 'running' else SURFACE_SOFT,
                outline=GREEN_100 if kind == 'running' else LINE, width=2)
        # 第一行：点 名字 状态 …… 启停按钮
        status_dot(d, (bx0 + 28, y + 40), 9, state_color(kind))
        name = t(f'mode.{mode}')
        draw_text(d, (bx0 + 48, y + 20), name, 28, INK, bold=True)
        nx = bx0 + 60 + text_width(name, 28, True)
        btn = (bx1 - 20 - 136, y + 18, bx1 - 20, y + 66)
        state_w = btn[0] - nx - 16
        draw_text(d, (nx, y + 28), fit(label, 18, state_w), 18, DANGER if kind == 'error' else INK_2)
        draw_text(d, (bx0 + 48, y + 64), fit(t(f'mode.desc.{mode}'), 16, btn[0] - bx0 - 64), 16, INK_3)
        is_busy = any(k.startswith('modes:') and mode in k.split(':')[1].split(',') for k in busy)
        pill(d, btn, t('btn.stop') if running else t('btn.start'), 20, busy=is_busy, danger=running,
             primary=not running, hover=hits.add(btn, ('mode', mode, 'stop' if running else 'start')))
        # 第二行：译成 + 语言
        d.line((bx0 + 20, y + 104, bx1 - 20, y + 104), fill=LINE, width=2)
        current = (payload.get(mode) or {}).get('target_language') or ''
        draw_text(d, (bx0 + 24, y + 128), t('label.to'), 17, INK_3, bold=True)
        box = (bx0 + 96, y + 120, bx0 + 96 + 240, y + 164)
        key = ('lang', mode)
        dropdown(d, box, lang_label(current), 18, is_open=open_menu == key, hover=hits.add(box, ('menu', key)))
        hits.anchors[key] = (box, lang_label(current), 18)
        if mode in ('others', 'screen'):
            _pager(d, hits, t, mode, payload, (scroll or {}).get(mode, 0), (box[2] + 14, box[1], bx1 - 20, box[3]))
        y += block_h + gap


def _pager(d, hits, t, mode, payload, scroll, area):
    """模式自己的翻页：翻页 ▲ 第几条/共几条 ▼。翻的是显示这个模式的手腕卡片（屏幕还有菜单页里的结果）。
    手腕卡片本身不能点（点了会让 VRChat 进 AFK，见 main.OverlayApp._create_overlays），所以翻页只在这里。"""
    x0, y0, x1, y1 = area
    limit = wrist_scroll_limit(mode, payload)
    scroll = max(0, min(int(scroll or 0), limit))
    pos, total = page_position(mode, payload, scroll)
    bw, text_w = 56, 84
    down = (x1 - bw, y0, x1, y1)
    up = (down[0] - text_w - bw, y0, down[0] - text_w, y1)
    if mode == 'others':
        up_enabled, down_enabled = scroll < limit, scroll > 0
    else:
        up_enabled, down_enabled = scroll > 0, scroll < limit
    for box, is_up, enabled in ((up, True, up_enabled), (down, False, down_enabled)):
        action = ('page', mode, 'up' if is_up else 'down')
        arrow_button(d, box, is_up, enabled, hover=hits.add(box, action) if enabled else False)
    text = f'{pos}/{total}' if total else '–'
    tw = text_width(text, 18, True)
    draw_text(d, ((up[2] + down[0] - tw) / 2, y0 + (y1 - y0 - line_height(18)) / 2 + 1), text, 18,
              PRIMARY if scroll else INK_2, bold=True)
    label = fit(t('label.page'), 17, up[0] - x0 - 10, True)
    draw_text(d, (up[0] - 10 - text_width(label, 17, True), y0 + 8), label, 17, INK_3, bold=True)


def _wrist_card(d, hits, t, settings):
    """手腕显示：左右手各显示什么、抬腕才显示，位置和角度在另一页（_wrist_adjust_card）里调。"""
    x0, x1 = COL_R
    bottom = TOP + 240
    y = _card(d, COL_R, TOP, bottom, t('card.wrist'), t('card.wrist.sub'))
    for side in ('left', 'right'):
        current = settings.get(f'{side}_wrist')
        draw_text(d, (x0 + 24, y + 8), t(f'label.{side}'), 18, INK_2, bold=True)
        cx = x0 + 100
        for content in ('others', 'self', 'screen', 'off'):
            box = (cx, y, cx + 94, y + 42)
            pill(d, box, t(f'mode.{content}'), 17, active=current == content,
                 hover=hits.add(box, ('wrist', side, content)))
            cx += 101
        y += 52
    y += 4
    gaze = bool(settings.get('gaze_only'))
    label = t('btn.gaze.on') if gaze else t('btn.gaze.off')
    box = (x0 + 24, y, x0 + 48 + int(text_width(label, 17, True)) + 16, y + 42)
    pill(d, box, label, 17, active=gaze, hover=hits.add(box, ('toggle', 'gaze_only')))
    rw = int(text_width(t('btn.adjustWrist'), 17, True)) + 48
    box = (x1 - 24 - rw, y, x1 - 24, y + 42)
    pill(d, box, t('btn.adjustWrist'), 17, primary=True, hover=hits.add(box, ('dash_page', 'wrist')))
    return bottom


def _cm(v):
    return f'{v * 100:.1f} cm'


def _deg(v):
    return f'{v:.0f}°'


def _pct(v):
    return f'{v * 100:.0f}%'


# 调整页的每一行：(设置键, 名字, 细调一步, 粗调一步, 数值格式, 按钮上步长的显示倍数)
WRIST_ADJUST_GROUPS = [
    ('group.position', [
        ('wrist_along', 'adj.along', 0.005, 0.02, _cm, 100),
        ('wrist_up', 'adj.up', 0.005, 0.02, _cm, 100),
        ('wrist_out', 'adj.out', 0.005, 0.02, _cm, 100),
    ]),
    ('group.angle', [
        ('wrist_roll', 'adj.roll', 5.0, 30.0, _deg, 1),
        ('wrist_pitch', 'adj.pitch', 5.0, 30.0, _deg, 1),
        ('wrist_yaw', 'adj.yaw', 5.0, 30.0, _deg, 1),
        ('wrist_spin', 'adj.spin', 5.0, 30.0, _deg, 1),
    ]),
    ('group.display', [
        ('wrist_width', 'adj.size', 0.005, 0.02, _cm, 100),
        ('wrist_alpha', 'adj.alpha', 0.05, 0.2, _pct, 100),
        ('wrist_font_scale', 'adj.font', 0.1, 0.3, _pct, 100),
    ]),
]


def _wrist_adjust_card(d, hits, t, settings, group_index=0):
    """调整手腕卡片，按组翻页（岚：按钮太多就做成翻页，上下翻）：位置（前后 / 上下 / 贴近）、
    角度（绕手臂 + 卡片自己的三个轴，可以转一整圈）、显示（大小 / 透明 / 字号）。
    每行上面是名字和数值，下面「−粗 −细 ＋细 ＋粗」。改了马上生效（settings.version 一变 main 就重新挂到手柄上）。"""
    x0, x1 = COL_R
    y = _card(d, COL_R, TOP, BOTTOM, t('card.wristAdjust'), '', reserve=160)
    bw = int(text_width(t('btn.done'), 18, True)) + 64
    box = (x1 - 24 - bw, TOP + 14, x1 - 24, TOP + 56)
    pill(d, box, t('btn.done'), 18, primary=True, hover=hits.add(box, ('dash_page', 'main')))

    left, right = x0 + 24, x1 - 24
    count = len(WRIST_ADJUST_GROUPS)
    group_index = max(0, min(int(group_index or 0), count - 1))
    group_key, rows = WRIST_ADJUST_GROUPS[group_index]

    # 翻页行：组名 …… ▲ 第几页/共几页 ▼
    bh = 48
    down = (right - 64, y, right, y + bh)
    up = (down[0] - 84 - 64, y, down[0] - 84, y + bh)
    for box, is_up, enabled in ((up, True, group_index > 0), (down, False, group_index < count - 1)):
        action = ('adjust_group', -1 if is_up else 1)
        arrow_button(d, box, is_up, enabled, hover=hits.add(box, action) if enabled else False)
    text = f'{group_index + 1}/{count}'
    draw_text(d, ((up[2] + down[0] - text_width(text, 18, True)) / 2, y + (bh - line_height(18)) / 2 + 1),
              text, 18, INK_2, bold=True)
    draw_text(d, (left, y + (bh - line_height(24)) / 2), fit(t(group_key), 24, up[0] - left - 12, True), 24,
              PRIMARY, bold=True)
    y += bh + 18
    d.line((left, y - 9, right, y - 9), fill=LINE, width=2)

    gap = 10
    btn_w = (right - left - gap * 3) / 4
    for key, label_key, fine, coarse, fmt, shown in rows:
        draw_text(d, (left, y + 4), fit(t(label_key), 19, right - left - 140, True), 19, INK_2, bold=True)
        value = fmt(float(settings.get(key) or 0))
        draw_text(d, (right - text_width(value, 20, True), y + 3), value, 20, INK, bold=True)
        by = y + 36
        for i, step in enumerate((-coarse, -fine, fine, coarse)):
            bx0 = left + i * (btn_w + gap)
            box = (int(bx0), by, int(bx0 + btn_w), by + 48)
            label = ('+' if step > 0 else '−') + f'{abs(step) * shown:g}'
            pill(d, box, label, 18, hover=hits.add(box, ('adjust', key, step)), radius=16)
        y += 36 + 48 + 22

    # 底部：位置复位 + 提示
    rw = int(text_width(t('btn.resetPose'), 17, True)) + 40
    by = BOTTOM - 20 - 40
    box = (left, by, left + rw, by + 40)
    pill(d, box, t('btn.resetPose'), 17, hover=hits.add(box, ('reset_wrist',)))
    hint = wrap(t('card.wristAdjust.hint'), 15, right - box[2] - 16, max_lines=2)
    hy = by + (40 - len(hint) * line_height(15)) / 2
    for ln in hint:
        draw_text(d, (box[2] + 16, hy), ln, 15, INK_3)
        hy += line_height(15)


def _screen_card(d, hits, t, payload, modes, busy, top, scroll=0):
    x0, x1 = COL_R
    y = _card(d, COL_R, top, BOTTOM, t('card.screen'), t('card.screen.sub'))
    screen = payload.get('screen') or {}
    screen_kind, _ = mode_state(modes, 'screen', t)
    bw = int(text_width(t('btn.translateScreen'), 19, True)) + 56
    box = (x0 + 24, y - 4, x0 + 24 + bw, y + 42)
    pill(d, box, t('btn.translateScreen'), 19, primary=True, hover=hits.add(box, ('screen_translate',)),
         busy='screen-translate' in busy or bool(screen.get('busy')))
    msg = screen.get('message') or ('' if screen_kind == 'running' else t('screen.notRunning'))
    if msg:
        draw_text(d, (box[2] + 16, y + 8), fit(msg, 16, x1 - box[2] - 44), 16,
                  DANGER if screen.get('status') == 'error' else INK_3)
    y += 54
    d.line((x0 + 24, y - 8, x1 - 24, y - 8), fill=LINE, width=2)
    items = [{'badge': '', 'original': it.get('original', ''), 'lang': it.get('lang', ''),
              'translated': it.get('translated', '')} for it in (screen.get('items') or [])]
    items = items[max(0, min(int(scroll or 0), len(items) - 1)):]   # 跟着屏幕模式的翻页走
    width = x1 - x0 - 48
    if not items:
        draw_text(d, (x0 + 24, y + 6), fit(t('screen.empty'), 17, width), 17, INK_4)
        return
    for entry, layout in _pick_entries(items, BOTTOM - 8 - y, width, newest_last=False, small=15, big=19, gap=8):
        _draw_entry(d, entry, layout, x0 + 24, y)
        y += layout[3]


def render_thumbnail(path: str) -> None:
    img = Image.new('RGBA', (256, 256), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    rounded(d, (8, 8, 248, 248), 56, fill=GREEN_600)
    tw = text_width('译', 150, True)
    draw_text(d, ((256 - tw) / 2, (256 - line_height(150)) / 2 - 6), '译', 150, SURFACE, bold=True)
    img.save(path)
