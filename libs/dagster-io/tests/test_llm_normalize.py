"""Tests for LLM text normalization — NFKC + control char removal."""

from dagster_io.llm import _normalize_messages, _normalize_text


def test_normalize_fullwidth_chars():
    """Fullwidth Unicode → ASCII equivalents."""
    assert _normalize_text("Rick Spence： CIA") == "Rick Spence: CIA"
    assert _normalize_text("Piers Morgan ｜ Full interview") == "Piers Morgan | Full interview"
    assert _normalize_text("S＊＊＊!") == "S***!"


def test_normalize_preserves_normal_text():
    """Normal ASCII text passes through unchanged."""
    text = "Joe Biden spoke at the UN General Assembly in 2024."
    assert _normalize_text(text) == text


def test_normalize_strips_null_bytes():
    """Null bytes and control chars are removed."""
    assert _normalize_text("hello\x00world") == "helloworld"
    assert _normalize_text("line1\nline2") == "line1\nline2"  # newlines preserved
    assert _normalize_text("tab\there") == "tab\there"  # tabs preserved


def test_normalize_messages_preserves_types():
    """Message normalization preserves message class types."""
    from langchain_core.messages import HumanMessage, SystemMessage

    messages = [
        SystemMessage(content="You are a system："),
        HumanMessage(content="Extract from this｜text"),
    ]
    normalized = _normalize_messages(messages)

    assert isinstance(normalized[0], SystemMessage)
    assert isinstance(normalized[1], HumanMessage)
    assert normalized[0].content == "You are a system:"
    assert normalized[1].content == "Extract from this|text"


def test_normalize_empty_text():
    assert _normalize_text("") == ""


def test_normalize_ligatures():
    """NFKC decomposes ligatures."""
    # ﬁ → fi, ﬂ → fl
    assert _normalize_text("ﬁnance ﬂow") == "finance flow"
