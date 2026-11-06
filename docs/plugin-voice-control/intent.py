"""自然语言模式：把说的话交给翻译用的大模型（ctx.llm_chat），让它回成命令 JSON。"""
from __future__ import annotations

import json
import re
from typing import List

MODES = ('self', 'others', 'screen')
TARGETS = ('main', 'secondary', 'others', 'screen')
SIDES = ('left', 'right', 'both')
CONTENTS = ('others', 'self', 'screen', 'off')

SYSTEM_PROMPT = """You turn a spoken request into commands for "Ganlan Translate", a VRChat translation app.
The speaker may start with the assistant's name ("{wake}"); ignore it. Reply with JSON only, no other text:
{{"commands": [ ... ]}}
Use an empty list when the request is not one of the supported commands (small talk, questions, anything else).

Supported commands:
- {{"action": "start", "modes": [...]}} start modes. Modes: "self" (translate what I say and send it to the VRChat chatbox),
  "others" (translate what other players say), "screen" (screen translation mode). "Everything" / "all features" = all three.
- {{"action": "stop", "modes": [...]}} stop modes.
- {{"action": "screen_translate"}} capture the game screen once and translate the text on it.
- {{"action": "language", "target": "main" | "secondary" | "others" | "screen", "language": "<code>"}}
  change a target language. main = the language my speech is translated into; secondary = the second output
  language ("" turns it off); others = the language other players are translated into; screen = screen translation.
  Codes: zh-CN, zh-TW, en, ja, ko, ru, es, fr, de, pt, it, id (or another BCP-47 code).
- {{"action": "wrist", "side": "left" | "right" | "both", "content": "others" | "self" | "screen" | "off"}}
  choose what the VR wrist card shows.

Several commands in one request are allowed, in the order they were asked.
Current state: {state}"""


def ask(ctx, text: str, wake_word: str, state: dict) -> List[dict]:
    """问大模型，返回整理好的命令列表（可能是空的）。出错抛异常。"""
    messages = [
        {'role': 'system', 'content': SYSTEM_PROMPT.format(wake=wake_word or '-',
                                                           state=json.dumps(state, ensure_ascii=False))},
        {'role': 'user', 'content': text},
    ]
    reply = ctx.llm_chat(messages, max_tokens=300, temperature=0.0, timeout=20.0)
    return parse(reply)


def parse(reply: str) -> List[dict]:
    """从模型的回复里取出命令，不认识的扔掉。"""
    text = str(reply or '')
    fence = re.search(r'```(?:json)?\s*(.*?)```', text, re.S)
    if fence:
        text = fence.group(1)
    start, end = text.find('{'), text.rfind('}')
    if start < 0 or end <= start:
        return []
    try:
        data = json.loads(text[start:end + 1])
    except ValueError:
        return []
    raw = data.get('commands') if isinstance(data, dict) else None
    if not isinstance(raw, list):
        return []
    commands = []
    for item in raw:
        command = _clean(item)
        if command is not None:
            commands.append(command)
    return commands


def _clean(item) -> dict | None:
    if not isinstance(item, dict):
        return None
    action = item.get('action')
    if action in ('start', 'stop'):
        modes = [m for m in item.get('modes') or [] if m in MODES]
        modes = list(dict.fromkeys(modes))
        return {'action': action, 'modes': modes} if modes else None
    if action == 'screen_translate':
        return {'action': 'screen_translate'}
    if action == 'language':
        target = item.get('target')
        language = str(item.get('language') or '').strip()
        if target not in TARGETS:
            return None
        if not language and target != 'secondary':
            return None
        if language and not re.fullmatch(r'[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8})*', language):
            return None
        return {'action': 'language', 'target': target, 'language': language}
    if action == 'wrist':
        side, content = item.get('side'), item.get('content')
        if side in SIDES and content in CONTENTS:
            return {'action': 'wrist', 'side': side, 'content': content}
    return None
