"""Tests for screen_translation.split_blocks: re-joining sentences the OCR model wrapped."""

from __future__ import annotations

import sys

import pytest

pytestmark = pytest.mark.skipif(sys.platform != 'win32', reason='screen capture is Windows only')

from screen_translation import split_blocks  # noqa: E402


def test_wrapped_japanese_bubble_is_one_block():
    text = "こんにちは、今日はとてもいい\n天気ですね。一緒にワールドを\n回りませんか？"
    assert split_blocks(text) == ["こんにちは、今日はとてもいい天気ですね。一緒にワールドを回りませんか？"]


def test_wrapped_chinese_paragraph_joins_until_sentence_end():
    text = "欢迎来到我们的世界，这里是\nVRChat 里最安静的地方\n请不要大声喧哗。\n祝你玩得开心！"
    assert split_blocks(text) == [
        "欢迎来到我们的世界，这里是VRChat 里最安静的地方请不要大声喧哗。",
        "祝你玩得开心！",
    ]


def test_wrapped_english_sentence_joins_with_spaces():
    text = "Welcome everyone to the Great Pug, please\nremember to be kind to each other and\nhave fun!"
    assert split_blocks(text) == [
        "Welcome everyone to the Great Pug, please remember to be kind to each other and have fun!",
    ]


def test_hyphenated_word_is_rejoined_without_space():
    assert split_blocks("This is a well-\nknown place.") == ["This is a well-known place."]


def test_short_labels_stay_separate():
    assert split_blocks("Settings\nProfile\nWorlds") == ["Settings", "Profile", "Worlds"]
    assert split_blocks("ワールド\nフレンド\nアバター") == ["ワールド", "フレンド", "アバター"]


def test_list_items_stay_separate():
    text = "ルール：\n1. 他の人に迷惑をかけないでください\n2. クラッシャーは禁止です\n・撮影は自由です"
    assert split_blocks(text) == [
        "ルール：",
        "1. 他の人に迷惑をかけないでください",
        "2. クラッシャーは禁止です",
        "・撮影は自由です",
    ]


def test_wrapped_list_item_continues():
    text = "1. Please do not use avatars that\ncrash other players.\n2. Have fun."
    assert split_blocks(text) == ["1. Please do not use avatars that crash other players.", "2. Have fun."]


def test_blank_lines_and_numbers_are_boundaries():
    text = "これは看板の一行目でまだ続く\n\nこれは別の吹き出しの文章です\n12:30\nまた別の文章がここにありま"
    assert split_blocks(text) == [
        "これは看板の一行目でまだ続く",
        "これは別の吹き出しの文章です",
        "また別の文章がここにありま",
    ]


def test_markdown_bullets_start_new_blocks_and_duplicates_drop():
    text = "- これはひとつめの項目の文章です\n- これはふたつめの項目の文章です\n- これはひとつめの項目の文章です"
    assert split_blocks(text) == ["これはひとつめの項目の文章です", "これはふたつめの項目の文章です"]


def test_korean_wrapped_line_joins_with_space():
    text = "안녕하세요 여러분 오늘 날씨가 정말\n좋네요"
    assert split_blocks(text) == ["안녕하세요 여러분 오늘 날씨가 정말 좋네요"]


def test_think_tags_and_empty_output():
    assert split_blocks("<think>reasoning</think>\nHello there.") == ["Hello there."]
    assert split_blocks("") == []
