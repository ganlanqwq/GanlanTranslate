"""口令表的解析和匹配。

比的时候先把文字「归一」：全角转半角、转小写、去掉空格和标点，汉字再转成不带声调的拼音
（识别常把「启动」写成「起动」、把「甘岚」写成「甘蓝」，按拼音比就都算对）。
口令里的 {lang} / {side} / {card} 是空位，能填的词见下面的 VOCAB。
"""
from __future__ import annotations

import re
import unicodedata
from typing import Dict, List, Optional, Tuple

try:
    from pypinyin import lazy_pinyin
except Exception:  # pragma: no cover - 甘岚翻译里都有 pypinyin
    lazy_pinyin = None

# 动作 → 执行时做什么。模式启停的 modes、改语言的 target、手腕卡片的 content 写在这里。
ACTIONS: Dict[str, dict] = {
    'start_self': {'action': 'start', 'modes': ['self']},
    'start_others': {'action': 'start', 'modes': ['others']},
    'start_screen': {'action': 'start', 'modes': ['screen']},
    'start_all': {'action': 'start', 'modes': ['self', 'others', 'screen']},
    'stop_self': {'action': 'stop', 'modes': ['self']},
    'stop_others': {'action': 'stop', 'modes': ['others']},
    'stop_screen': {'action': 'stop', 'modes': ['screen']},
    'stop_all': {'action': 'stop', 'modes': ['self', 'others', 'screen']},
    'screen_translate': {'action': 'screen_translate'},
    'target': {'action': 'language', 'target': 'main'},
    'secondary': {'action': 'language', 'target': 'secondary'},
    'secondary_off': {'action': 'language', 'target': 'secondary', 'language': ''},
    'others_lang': {'action': 'language', 'target': 'others'},
    'screen_lang': {'action': 'language', 'target': 'screen'},
    'wrist': {'action': 'wrist'},
    'wrist_off': {'action': 'wrist', 'content': 'off'},
}

# 空位能填的词 → 值（中、英、日、韩的说法）
VOCAB: Dict[str, Dict[str, str]] = {
    'lang': {
        '中文': 'zh-CN', '汉语': 'zh-CN', '简体中文': 'zh-CN', '简体': 'zh-CN', '普通话': 'zh-CN',
        '繁体中文': 'zh-TW', '繁体': 'zh-TW', '繁中': 'zh-TW',
        '英语': 'en', '英文': 'en', '日语': 'ja', '日文': 'ja', '韩语': 'ko', '韩文': 'ko', '朝鲜语': 'ko',
        '俄语': 'ru', '西班牙语': 'es', '法语': 'fr', '德语': 'de', '葡萄牙语': 'pt', '意大利语': 'it',
        '印尼语': 'id', '印度尼西亚语': 'id',
        'chinese': 'zh-CN', 'simplified chinese': 'zh-CN', 'traditional chinese': 'zh-TW',
        'english': 'en', 'japanese': 'ja', 'korean': 'ko', 'russian': 'ru', 'spanish': 'es',
        'french': 'fr', 'german': 'de', 'portuguese': 'pt', 'italian': 'it', 'indonesian': 'id',
        '中国語': 'zh-CN', '簡体字': 'zh-CN', '繁体字': 'zh-TW', '英語': 'en', '日本語': 'ja', '韓国語': 'ko',
        'ロシア語': 'ru', 'スペイン語': 'es', 'フランス語': 'fr', 'ドイツ語': 'de', 'ポルトガル語': 'pt',
        'イタリア語': 'it', 'インドネシア語': 'id',
        '중국어': 'zh-CN', '영어': 'en', '일본어': 'ja', '한국어': 'ko', '러시아어': 'ru', '스페인어': 'es',
        '프랑스어': 'fr', '독일어': 'de', '포르투갈어': 'pt', '이탈리아어': 'it', '인도네시아어': 'id',
    },
    'side': {
        '左手': 'left', '左边': 'left', '左手腕': 'left', '右手': 'right', '右边': 'right', '右手腕': 'right',
        '双手': 'both', '两只手': 'both', '两手': 'both', '两边': 'both',
        'left': 'left', 'left hand': 'left', 'left wrist': 'left',
        'right': 'right', 'right hand': 'right', 'right wrist': 'right',
        'both': 'both', 'both hands': 'both', 'both wrists': 'both',
        '左': 'left', '右': 'right', '両手': 'both',
        '왼손': 'left', '왼쪽': 'left', '오른손': 'right', '오른쪽': 'right', '양손': 'both',
    },
    'card': {
        '他人': 'others', '翻译他人': 'others', '别人': 'others', '自我': 'self', '自己': 'self',
        '自我翻译': 'self', '屏幕': 'screen', '画面': 'screen', '翻译画面': 'screen',
        '关闭': 'off', '不显示': 'off', '空白': 'off',
        'others': 'others', 'other people': 'others', 'self': 'self', 'myself': 'self', 'me': 'self',
        'screen': 'screen', 'nothing': 'off', 'none': 'off', 'off': 'off',
        '相手': 'others', '自分': 'self', 'なし': 'off', 'オフ': 'off',
        '상대': 'others', '다른 사람': 'others', '나': 'self', '자기': 'self', '화면': 'screen', '끄기': 'off',
    },
}

SLOT_RE = re.compile(r'\{(lang|side|card)\}')

DEFAULT_TABLE = """# 一行一个动作：动作 = 口令 | 口令 | …（# 开头是说明）。说的整句话和某个口令一样才算（不管标点、空格，同音字也算）。
# 空位：{lang} 语言（日语、英语、中文……）；{side} 左手 / 右手 / 双手；{card} 他人 / 自我 / 屏幕 / 关闭
start_self = 启动自我翻译 | 开启自我翻译 | 打开自我翻译 | 开始自我翻译
start_others = 启动翻译他人 | 开启翻译他人 | 打开翻译他人 | 开始翻译他人 | 启动他人翻译 | 开启他人翻译 | 打开他人翻译
start_screen = 启动翻译画面 | 开启翻译画面 | 打开翻译画面 | 启动屏幕翻译 | 开启屏幕翻译 | 打开屏幕翻译
start_all = 启动所有功能 | 开启所有功能 | 打开所有功能 | 启动全部功能 | 全部启动
stop_self = 关闭自我翻译 | 停止自我翻译 | 关掉自我翻译
stop_others = 关闭翻译他人 | 停止翻译他人 | 关掉翻译他人 | 关闭他人翻译 | 停止他人翻译
stop_screen = 关闭翻译画面 | 停止翻译画面 | 关掉翻译画面 | 关闭屏幕翻译 | 停止屏幕翻译
stop_all = 关闭所有功能 | 停止所有功能 | 关闭全部功能 | 全部关闭 | 全部停止
screen_translate = 翻译一下画面 | 翻译一下屏幕 | 翻译当前画面 | 识别画面 | 截图翻译
target = 主语言改成{lang} | 主语言换成{lang} | 主语言设为{lang} | 翻译成{lang}
secondary = 第二语言改成{lang} | 第二语言换成{lang} | 第二语言设为{lang}
secondary_off = 关闭第二语言 | 取消第二语言 | 不要第二语言
others_lang = 他人翻译成{lang} | 他人语言改成{lang}
screen_lang = 画面翻译成{lang} | 画面语言改成{lang}
wrist = {side}显示{card} | {side}手腕显示{card} | {side}卡片显示{card}
wrist_off = 关闭{side}卡片 | 关闭{side}手腕 | {side}不显示
# English
start_self = start self translation | turn on self translation
start_others = start translating others | turn on translating others | start others translation
start_screen = start screen translation | turn on screen translation
start_all = start everything | turn everything on | start all
stop_self = stop self translation | turn off self translation
stop_others = stop translating others | turn off translating others | stop others translation
stop_screen = stop screen translation | turn off screen translation
stop_all = stop everything | turn everything off | stop all
screen_translate = translate the screen | translate screen
target = translate to {lang} | set main language to {lang} | change main language to {lang}
secondary = set second language to {lang} | change second language to {lang}
secondary_off = turn off second language | no second language
others_lang = translate others to {lang}
screen_lang = translate screen to {lang}
wrist = show {card} on {side} | show {card} on {side} wrist
wrist_off = hide {side} wrist | turn off {side} wrist
# 日本語
start_self = 自分の翻訳を開始 | 自分の翻訳をオン
start_others = 相手の翻訳を開始 | 他人の翻訳を開始 | 相手の翻訳をオン
start_screen = 画面翻訳を開始 | 画面の翻訳を開始 | 画面翻訳をオン
start_all = 全部開始 | すべて開始
stop_self = 自分の翻訳を停止 | 自分の翻訳をオフ
stop_others = 相手の翻訳を停止 | 他人の翻訳を停止 | 相手の翻訳をオフ
stop_screen = 画面翻訳を停止 | 画面の翻訳を停止 | 画面翻訳をオフ
stop_all = 全部停止 | すべて停止
screen_translate = 画面を翻訳して | 画面を翻訳
target = {lang}に翻訳して | メイン言語を{lang}に
secondary = 第二言語を{lang}に
secondary_off = 第二言語をオフ
wrist = {side}に{card}を表示
wrist_off = {side}の表示をオフ
# 한국어
start_self = 내 번역 시작 | 자기 번역 시작
start_others = 상대 번역 시작 | 다른 사람 번역 시작
start_screen = 화면 번역 시작
start_all = 전부 시작 | 모두 시작
stop_self = 내 번역 중지 | 자기 번역 중지
stop_others = 상대 번역 중지 | 다른 사람 번역 중지
stop_screen = 화면 번역 중지
stop_all = 전부 중지 | 모두 중지
screen_translate = 화면 번역해 줘 | 화면 번역하기
target = {lang}로 번역 | 주 언어를 {lang}로
secondary = 두 번째 언어를 {lang}로
secondary_off = 두 번째 언어 끄기
wrist = {side}에 {card} 표시
wrist_off = {side} 표시 끄기
"""


def normalize(text: str) -> str:
    """全角转半角、小写、只留字母和数字（去掉空格、标点、符号）。"""
    text = unicodedata.normalize('NFKC', str(text or '')).lower()
    return ''.join(ch for ch in text if unicodedata.category(ch)[0] in 'LNM')


def key(text: str) -> str:
    """比较用的形式：normalize 之后汉字换成拼音（不带声调）。"""
    text = normalize(text)
    if not text or lazy_pinyin is None or not any('㐀' <= ch <= '鿿' for ch in text):
        return text
    return ''.join(lazy_pinyin(text))


class Template:
    """一条口令，拆成「文字」和「空位」交替的几段，都是 key() 之后的样子。"""

    def __init__(self, action: str, phrase: str, vocab: Dict[str, Dict[str, str]]):
        self.action = action
        self.phrase = phrase.strip()
        self.parts: List[Tuple[str, str]] = []   # ('text', 'qidong') / ('slot', 'lang')
        pos = 0
        for m in SLOT_RE.finditer(self.phrase):
            literal = key(self.phrase[pos:m.start()])
            if literal:
                self.parts.append(('text', literal))
            self.parts.append(('slot', m.group(1)))
            pos = m.end()
        literal = key(self.phrase[pos:])
        if literal:
            self.parts.append(('text', literal))
        self.vocab = vocab

    def match(self, k: str) -> Optional[dict]:
        return _match(k, self.parts, self.vocab, {})

    def could_start(self, k: str) -> bool:
        """k 是不是这条口令说到一半的样子（半句用）。"""
        return _viable(k, self.parts, self.vocab)


def _match(k: str, parts, vocab, slots: dict) -> Optional[dict]:
    if not parts:
        return slots if not k else None
    kind, value = parts[0]
    if kind == 'text':
        if k.startswith(value):
            return _match(k[len(value):], parts[1:], vocab, slots)
        return None
    for word, resolved in vocab[value].items():
        if word and k.startswith(word):
            found = _match(k[len(word):], parts[1:], vocab, {**slots, value: resolved})
            if found is not None:
                return found
    return None


def _viable(k: str, parts, vocab) -> bool:
    if not k:
        return True
    if not parts:
        return False
    kind, value = parts[0]
    if kind == 'text':
        if value.startswith(k):
            return True
        return k.startswith(value) and _viable(k[len(value):], parts[1:], vocab)
    for word in vocab[value]:
        if word.startswith(k):
            return True
        if word and k.startswith(word) and _viable(k[len(word):], parts[1:], vocab):
            return True
    return False


def _keyed_vocab() -> Dict[str, Dict[str, str]]:
    keyed: Dict[str, Dict[str, str]] = {}
    for slot, words in VOCAB.items():
        # 长的词先试（「左手腕」比「左手」先）
        items = sorted(((key(w), v) for w, v in words.items()), key=lambda item: -len(item[0]))
        keyed[slot] = {w: v for w, v in items if w}
    return keyed


def parse_table(text: str) -> Tuple[List[Template], List[str]]:
    """解析口令表，返回 (口令, 看不懂的行)。空表用默认的。"""
    if not str(text or '').strip():
        text = DEFAULT_TABLE
    vocab = _keyed_vocab()
    templates: List[Template] = []
    problems: List[str] = []
    for raw in str(text).splitlines():
        line = raw.strip()
        if not line or line.startswith('#'):
            continue
        action, sep, phrases = line.partition('=')
        action = action.strip()
        if not sep or action not in ACTIONS:
            problems.append(line)
            continue
        for phrase in phrases.split('|'):
            if phrase.strip():
                template = Template(action, phrase, vocab)
                if template.parts:
                    templates.append(template)
    return templates, problems


def build_command(action: str, slots: dict) -> Optional[dict]:
    """动作 + 空位 → 要执行的命令；缺空位返回 None。"""
    command = dict(ACTIONS[action])
    if command['action'] == 'language' and 'language' not in command:
        if 'lang' not in slots:
            return None
        command['language'] = slots['lang']
    if command['action'] == 'wrist':
        if 'side' not in slots:
            return None
        command['side'] = slots['side']
        if 'content' not in command:
            if 'card' not in slots:
                return None
            command['content'] = slots['card']
    if 'modes' in command:
        command['modes'] = list(command['modes'])
    return command


class Matcher:
    def __init__(self, table_text: str, wake_word: str = ''):
        self.templates, self.problems = parse_table(table_text)
        self.wake = key(wake_word)

    def strip_wake(self, k: str) -> Tuple[bool, str]:
        """(说了唤醒词没有, 去掉唤醒词后的 key)。"""
        if self.wake and k.startswith(self.wake):
            return True, k[len(self.wake):]
        return False, k

    def match_key(self, k: str) -> Optional[dict]:
        for template in self.templates:
            slots = template.match(k)
            if slots is not None:
                command = build_command(template.action, slots)
                if command is not None:
                    command['phrase'] = template.phrase
                    return command
        return None

    def could_be_command(self, k: str) -> bool:
        return any(t.could_start(k) for t in self.templates)

    def could_be_wake(self, k: str) -> bool:
        return bool(self.wake) and (self.wake.startswith(k) or k.startswith(self.wake))
