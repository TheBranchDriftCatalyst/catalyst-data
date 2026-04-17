"""Tests for transcription text normalization (_normalize_text).

Covers: ALL CAPS → sentence case, missing punctuation spacing,
camelCase word splits, and passthrough of normal text.
"""


from media_ingest.assets.transcription import _normalize_text


class TestNormalizeText:
    """Post-processing fixes for Whisper/OpenVINO transcription artifacts."""

    def test_passthrough_normal_text(self):
        assert _normalize_text("Normal sentence that should not change.") == "Normal sentence that should not change."

    def test_passthrough_short_text(self):
        assert _normalize_text("Hi") == "Hi"

    def test_passthrough_empty(self):
        assert _normalize_text("") == ""
        assert _normalize_text(None) is None

    # ── ALL CAPS → sentence case ────────────────────────────────────────

    def test_all_caps_sentence_cased(self):
        text = "IMPORTANTLY, HE IS A PRODUCT OF A CULTURE WHICH HAS TURNED ON ITS OWN."
        result = _normalize_text(text)
        assert result[0] == "I"
        assert result[1:].islower() or not result.isupper()
        assert "importantly" in result.lower()

    def test_all_caps_multi_sentence(self):
        text = "HE IS EITHER A LITMUS TEST. HE IS ALSO A WALKING MANIFESTATION OF SOMETHING."
        result = _normalize_text(text)
        # Each sentence should start with uppercase
        assert result.startswith("He is either")
        assert "He is also" in result

    def test_mixed_case_not_converted(self):
        """Text with normal mixed case should not be sentence-cased."""
        text = "Nick Fuentes really passed that test with flying colors. That fragmentation is being caused purposefully."
        assert _normalize_text(text) == text

    def test_short_caps_not_converted(self):
        """Short ALL CAPS strings (<30 alpha chars) should stay as-is."""
        assert _normalize_text("THIS IS SHORT") == "THIS IS SHORT"

    # ── Missing space after punctuation ─────────────────────────────────

    def test_space_after_period(self):
        assert _normalize_text("Fuentes.The main reason") == "Fuentes. The main reason"

    def test_space_after_exclamation(self):
        assert _normalize_text("incredible!But then") == "incredible! But then"

    def test_space_after_question(self):
        assert _normalize_text("really?The answer") == "really? The answer"

    def test_space_after_comma(self):
        assert _normalize_text("people,especially young men") == "people, especially young men"

    def test_multiple_punctuation_fixes(self):
        result = _normalize_text("first.Second,third!Fourth?Fifth")
        assert result == "first. Second, third! Fourth? Fifth"

    # ── camelCase word splits ───────────────────────────────────────────

    def test_camel_case_split(self):
        assert _normalize_text("NickFuentes") == "Nick Fuentes"

    def test_camel_case_in_sentence(self):
        result = _normalize_text("talking about NickFuentes on the show")
        assert "Nick Fuentes" in result

    def test_camel_case_multiple(self):
        result = _normalize_text("JoeBiden met with BarackObama")
        assert "Joe Biden" in result
        assert "Barack Obama" in result

    # ── Combined fixes ──────────────────────────────────────────────────

    def test_combined_caps_and_punctuation(self):
        text = "THE TRUTH IS IT DOESN'T MAKE A LOT OF SENSE.PEOPLE WHO SAY YOU SHOULDN'T GIVE HIM A PLATFORM."
        result = _normalize_text(text)
        assert not result.isupper()
        assert ". " in result  # period spacing fixed

    def test_realistic_transcript_fragment(self):
        """Real example from CNN Piers Morgan transcript."""
        text = "hearingabout him is because he'spopular.A large number of people,especially young men"
        result = _normalize_text(text)
        # Punctuation and comma spacing fixed
        assert ". A large" in result
        assert ", especially" in result
