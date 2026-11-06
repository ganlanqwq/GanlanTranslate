"""Tests for simultaneous dual-target-language translation."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DOTENV_PATH = PROJECT_ROOT / ".env"
if DOTENV_PATH.exists():
    for line in DOTENV_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip("'\"").strip()
        if key and key not in os.environ:
            os.environ[key] = value



class TestDualTargetPipeline:
    """Verify both translators are created and can translate independently."""

    def test_reinitialize_with_dual_target(self):
        from streaming_translation import (
            TranslationConfig,
            reinitialize_translator,
        )

        class MockState:
            pass

        state = MockState()
        cfg = TranslationConfig(
            target_language="ja",
            secondary_target_language="ko",
            translation_api_type="qwen_mt",
            dashscope_api_key="test-key",
        )

        with patch("streaming_translation.api.qwen_mt.OpenAI") as mock_openai:
            mock_openai.return_value = MagicMock()
            reinitialize_translator(state, cfg)

        assert state.translator is not None, "Primary translator not created"
        assert state.secondary_translator is not None, "Secondary translator not created"
        assert state.target_language == "ja"
        assert state.secondary_target_language == "ko"

    def test_both_translators_translate_independently(self):
        from streaming_translation import (
            BaseTranslationAPI,
            ContextAwareTranslator,
        )

        class MockDualAPI(BaseTranslationAPI):
            SUPPORTS_CONTEXT = True
            def __init__(self, tag=""):
                self.tag = tag
                self.calls = []
            def translate(self, text, **kw):
                tgt = kw.get("target_language", "?")
                self.calls.append((text, tgt))
                return f"[{tgt}] {text}"

        primary_api = MockDualAPI()
        secondary_api = MockDualAPI()

        primary = ContextAwareTranslator(primary_api, target_language="ja")
        secondary = ContextAwareTranslator(secondary_api, target_language="ko")

        r1 = primary.translate("Hello", source_language="en")
        r2 = secondary.translate("Hello", source_language="en")

        assert r1 == "[ja] Hello"
        assert r2 == "[ko] Hello"
        assert len(primary_api.calls) == 1
        assert len(secondary_api.calls) == 1

    def test_dual_translators_maintain_separate_contexts(self):
        from streaming_translation import (
            BaseTranslationAPI,
            ContextAwareTranslator,
        )

        class MockAPI(BaseTranslationAPI):
            SUPPORTS_CONTEXT = True
            def translate(self, text, **kw):
                return kw.get("target_language", "?") + ": " + text

        api1 = MockAPI()
        api2 = MockAPI()

        t1 = ContextAwareTranslator(api1, target_language="ja", max_context_size=3)
        t2 = ContextAwareTranslator(api2, target_language="ko", max_context_size=3)

        for i in range(3):
            t1.translate(f"msg {i}")
            t2.translate(f"MSG {i}")

        ctx1 = t1.get_contexts()
        ctx2 = t2.get_contexts()

        assert len(ctx1) == 3
        assert len(ctx2) == 3
        assert ctx1[0]["source"].startswith("msg")
        assert ctx2[0]["source"].startswith("MSG")

    def test_dual_translate_with_backend(self):
        from streaming_translation import (
            BaseTranslationAPI,
            ContextAwareTranslator,
            translate_with_backend,
        )

        class MockAPI(BaseTranslationAPI):
            SUPPORTS_CONTEXT = True
            def translate(self, text, **kw):
                tgt = kw.get("target_language", "?")
                return f"[{tgt}] {text}"

        p = ContextAwareTranslator(MockAPI(), target_language="ja")
        s = ContextAwareTranslator(MockAPI(), target_language="ko")

        r1 = translate_with_backend(p, "hello", "ja",
                                    source_language="en", context_prefix="")
        r2 = translate_with_backend(s, "hello", "ko",
                                    source_language="en", context_prefix="")

        assert r1 == "[ja] hello"
        assert r2 == "[ko] hello"

    def test_secondary_ensure_recreates_on_language_change(self):
        from streaming_translation import (
            TranslationConfig,
            ensure_secondary_translator,
        )

        class MockState:
            def __init__(self):
                self.secondary_translator = None
                self.secondary_translation_api = None
                self.secondary_target_language = None

        cfg = TranslationConfig(
            translation_api_type="qwen_mt",
            dashscope_api_key="test-key",
        )
        state = MockState()

        with patch("streaming_translation.api.qwen_mt.OpenAI") as mock_openai:
            mock_openai.return_value = MagicMock()
            ok1 = ensure_secondary_translator(state, "ja", cfg)
            assert ok1 is True
            assert state.secondary_target_language == "ja"

            ok2 = ensure_secondary_translator(state, "de", cfg)
            assert ok2 is True
            assert state.secondary_target_language == "de"

            ok3 = ensure_secondary_translator(state, "de", cfg)
            assert ok3 is True

    def test_secondary_removed_when_target_none(self):
        from streaming_translation import (
            TranslationConfig,
            ensure_secondary_translator,
        )

        class MockState:
            def __init__(self):
                self.secondary_translator = MagicMock()
                self.secondary_translation_api = MagicMock()
                self.secondary_target_language = "ja"

        cfg = TranslationConfig()
        state = MockState()
        result = ensure_secondary_translator(state, None, cfg)
        assert result is False
        assert state.secondary_translator is None
        assert state.secondary_target_language is None


class TestTranslateWithBackendCallPattern:
    def test_no_duplicate_arg_error(self):
        from streaming_translation import (
            BaseTranslationAPI,
            ContextAwareTranslator,
            translate_with_backend,
        )

        class Mock(BaseTranslationAPI):
            SUPPORTS_CONTEXT = True
            def translate(self, text, **kw):
                sl = kw.get("source_language", "NOT_SET")
                detected = kw.get("detected_source_language", "NOT_SET")
                return f"sl={sl} detected={detected} text={text}"

        t = ContextAwareTranslator(Mock(), target_language="ja")
        result = translate_with_backend(
            t, "hello", "ja", None, None, "en",
            source_language="auto", context_prefix="VRChat:",
            record_history=False,
        )
        assert not result.startswith("[ERROR]")
        assert "sl=auto" in result
        assert "detected=en" in result
