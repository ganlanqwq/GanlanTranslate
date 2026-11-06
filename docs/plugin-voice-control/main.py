"""语音控制：用说的话控制甘岚翻译（启停模式、翻译画面、改译文语言、VR 手腕卡片）。

- 指定口令：说的整句话和口令表里某一句一样（不管标点、空格，同音字也算）才执行，不用大模型。
- 自然语言：先说唤醒词，后面的话交给「API 配置」里翻译用的大模型理解意思。

口令那句话不翻译、不发聊天框（filter_source 返回 ""），说到一半的半句也不显示、不发（filter_partial_source）。
真正去做的事放在插件自己的线程里（启停模式要等好几秒，不能卡住识别）。
"""
from __future__ import annotations

import queue
import threading
import time

from . import commands
from . import intent

MODE_NAMES = {'self': '自我翻译', 'others': '翻译他人', 'screen': '翻译画面'}
TARGET_NAMES = {'main': '主语言', 'secondary': '第二语言', 'others': '他人译文语言', 'screen': '画面译文语言'}
SIDE_NAMES = {'left': '左手', 'right': '右手', 'both': '双手'}
CONTENT_NAMES = {'others': '他人', 'self': '自我', 'screen': '画面', 'off': '不显示'}
LANGUAGE_PATHS = {
    'main': ('/api/target-language', 'target_language'),
    'secondary': ('/api/secondary-target-language', 'secondary_target_language'),
    'others': ('/api/others-target-language', 'target_language'),
    'screen': ('/api/screen-target-language', 'target_language'),
}

ctx = None
_matcher = None
_jobs: "queue.Queue" = queue.Queue()
_worker = None


# ---------------------------------------------------------------- 生命周期

def setup(context):
    global ctx, _worker
    ctx = context
    _rebuild()
    _worker = threading.Thread(target=_run, name='voice-control', daemon=True)
    _worker.start()


def teardown():
    _jobs.put(None)


def on_settings_changed(settings):
    _rebuild()


def _settings():
    s = ctx.settings
    return (s.get('mode') or 'fixed'), str(s.get('wake_word') or '').strip(), bool(s.get('fixed_need_wake'))


def _rebuild():
    global _matcher
    mode, wake, need_wake = _settings()
    matcher = commands.Matcher(ctx.settings.get('phrases') or '', wake)
    _matcher = matcher
    if mode == 'natural':
        how = f'自然语言（先说「{wake}」）' if wake else '自然语言（没填唤醒词，不会触发）'
    else:
        how = '指定口令' + (f'（先说「{wake}」）' if need_wake and wake else '')
    ctx.log(f'{how}，口令 {len(matcher.templates)} 条')
    for line in matcher.problems[:5]:
        ctx.log(f'口令表这一行看不懂，已跳过：{line}')


# ---------------------------------------------------------------- 钩子（识别线程里，要快）

def filter_source(text):
    matcher = _matcher
    if matcher is None:
        return None
    k = commands.key(text)
    if not k:
        return None
    mode, wake, need_wake = _settings()
    said_wake, rest = matcher.strip_wake(k)
    if mode == 'natural':
        if not said_wake:
            return None
        command = matcher.match_key(rest) if rest else None
        if command is not None:
            _jobs.put(('run', text, [command]))
        elif rest:
            _jobs.put(('ask', text, wake))
        else:
            ctx.log('只听到唤醒词')
        return ''
    if need_wake and not said_wake:
        return None
    command = matcher.match_key(rest)
    if command is None:
        return None
    _jobs.put(('run', text, [command]))
    return ''


def filter_partial_source(text):
    """说到一半：像口令（或唤醒词）的开头就先不显示、不发，免得口令被发到聊天框。"""
    matcher = _matcher
    if matcher is None:
        return None
    k = commands.key(text)
    if not k:
        return None
    mode, _wake, need_wake = _settings()
    if mode == 'natural' or need_wake:
        return '' if matcher.could_be_wake(k) else None
    said_wake, rest = matcher.strip_wake(k)
    if said_wake and not rest:
        return ''
    if not said_wake and matcher.could_be_wake(k):
        return ''
    return '' if matcher.could_be_command(rest) else None


# ---------------------------------------------------------------- 执行（插件自己的线程）

def _run():
    while True:
        job = _jobs.get()
        if job is None:
            return
        try:
            kind, text = job[0], job[1]
            if kind == 'ask':
                ctx.log(f'听到「{text}」，让大模型理解意思…')
                try:
                    found = intent.ask(ctx, text, job[2], _state_for_llm())
                except Exception as exc:
                    ctx.log(f'大模型没回成：{exc}')
                    continue
                if not found:
                    ctx.log('大模型说这不是能执行的命令')
                    continue
            else:
                found = job[2]
                ctx.log(f'听到口令「{text}」')
            for command in found:
                _execute(command)
        except Exception as exc:
            ctx.log(f'执行出错：{exc}')


def _state_for_llm() -> dict:
    try:
        status = ctx.call_api('GET', '/api/status')
    except Exception:
        return {}
    return {
        'modes': status.get('modes') or {},
        'main_language': status.get('target_language') or '',
        'secondary_language': status.get('secondary_target_language') or '',
        'others_language': status.get('others_target_language') or '',
        'screen_language': status.get('screen_target_language') or '',
        'display_mode': status.get('display_mode') or '',
    }


def _names(modes):
    return '、'.join(MODE_NAMES.get(m, m) for m in modes)


def _result(label: str, response: dict) -> None:
    ok = response.get('success') and response.get('http_status', 200) < 400
    message = response.get('message') or ''
    ctx.log(f'{label}：' + ('好了' if ok else f'没成功（{message or response.get("http_status")}）'))


def _execute(command: dict) -> None:
    action = command.get('action')
    if action == 'start':
        _result(f'启动{_names(command["modes"])}', ctx.call_api('POST', '/api/modes/start', {'modes': command['modes']}))
    elif action == 'stop':
        _result(f'关闭{_names(command["modes"])}', ctx.call_api('POST', '/api/modes/stop', {'modes': command['modes']}))
    elif action == 'screen_translate':
        _screen_translate()
    elif action == 'language':
        target, language = command['target'], command.get('language') or ''
        path, field = LANGUAGE_PATHS[target]
        label = f'{TARGET_NAMES[target]}改成 {language}' if language else f'关闭{TARGET_NAMES[target]}'
        _result(label, ctx.call_api('POST', path, {field: language}))
    elif action == 'wrist':
        side, content = command['side'], command['content']
        response = ctx.call_api('POST', '/api/vr/wrist', {'side': side, 'content': content})
        _result(f'{SIDE_NAMES[side]}手腕卡片显示{CONTENT_NAMES[content]}', response)
        if response.get('success') and not response.get('overlay_connected'):
            ctx.log('VR 附属程序现在没连上，这次设置不会生效（要在 VR 显示方式下、附属程序开着）')


def _mode_status(mode: str) -> str:
    try:
        return str((ctx.call_api('GET', '/api/status').get('modes') or {}).get(mode) or '')
    except Exception:
        return ''


def _screen_translate() -> None:
    """翻译一次画面。屏幕模式没开就先开，等它跑起来再截。"""
    if _mode_status('screen') != 'running':
        ctx.log('屏幕模式没开，先启动它')
        response = ctx.call_api('POST', '/api/modes/start', {'modes': ['screen']})
        if not response.get('success'):
            _result('启动翻译画面', response)
            return
        deadline = time.time() + 20
        while time.time() < deadline and _mode_status('screen') != 'running':
            time.sleep(0.5)
    _result('翻译一次画面', ctx.call_api('POST', '/api/screen-translate'))
