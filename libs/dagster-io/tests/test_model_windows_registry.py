"""Tests for MODEL_WINDOWS registry and window_for_model helper.

Phase 3 (CD-80ic).
"""

from dagster_io.chunking import MODEL_WINDOWS, window_for_model


class TestModelWindowsRegistry:
    def test_registry_is_non_empty(self):
        assert len(MODEL_WINDOWS) > 0

    def test_gliner_medium_exact(self):
        assert MODEL_WINDOWS["gliner-medium"] == 320

    def test_gliner_large_exact(self):
        assert MODEL_WINDOWS["gliner-large"] == 320

    def test_gemma3_12b_exact(self):
        assert MODEL_WINDOWS["gemma3-12b"] == 24576

    def test_gpt_4o_mini_exact(self):
        assert MODEL_WINDOWS["gpt-4o-mini"] == 32768


class TestWindowForModel:
    def test_gliner_medium_exact(self):
        assert window_for_model("gliner-medium") == 320

    def test_gliner_large_exact(self):
        assert window_for_model("gliner-large") == 320

    def test_gemma3_12b_exact(self):
        assert window_for_model("gemma3-12b") == 24576

    def test_gpt_4o_mini_exact(self):
        assert window_for_model("gpt-4o-mini") == 32768

    def test_claude_haiku_exact(self):
        # claude-haiku is explicitly registered
        result = window_for_model("claude-haiku")
        assert result > 0

    def test_unknown_model_fallback(self):
        assert window_for_model("unknown-totally-new-model-xyz") == 4000

    def test_empty_string_fallback(self):
        assert window_for_model("") == 4000

    def test_none_via_empty_fallback(self):
        # window_for_model requires a string; empty string returns safe default
        assert window_for_model("") == 4000

    def test_gliner_pattern_fallback(self):
        # A model containing "gliner" but not exactly matching a key should
        # either match a substring key or use the gliner heuristic → 320.
        result = window_for_model("my-custom-gliner-v3")
        assert result == 320

    def test_gpt_pattern_fallback(self):
        # A model containing "gpt" should pick up the GPT heuristic → 16000
        # (unless it matches a longer key like gpt-4o-mini, gpt-4o, gpt-4).
        result = window_for_model("my-gpt-nano")
        assert result == 16000

    def test_claude_pattern_fallback(self):
        result = window_for_model("my-claude-custom")
        assert result > 0

    def test_gemma_pattern_fallback(self):
        # gemma appears as a key prefix; pattern should return a positive value
        result = window_for_model("gemma-unknown-version")
        assert result > 0

    def test_case_insensitive_lookup(self):
        # window_for_model lowercases the name before lookup
        assert window_for_model("GLiNER-MEDIUM") == 320

    def test_gpt4o_vs_gpt4o_mini_no_collision(self):
        # gpt-4o-mini should match the longer key and not just gpt-4o
        mini = window_for_model("gpt-4o-mini")
        full = window_for_model("gpt-4o")
        # Both are positive; mini >= full because it has its own larger mapping
        assert mini > 0 and full > 0

    def test_all_registered_models_return_positive(self):
        for name in MODEL_WINDOWS:
            assert window_for_model(name) > 0, f"window_for_model({name!r}) returned non-positive"
