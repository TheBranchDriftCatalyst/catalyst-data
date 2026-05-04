"""Shared text utilities — normalization, cleaning.

Functions here are used at both ingestion (chunking) and LLM boundaries.
"""

from __future__ import annotations

import unicodedata

# Targeted character map for chars that NFKC leaves alone but our
# SentencePiece-backed tokenizers (gliner, nuextract, universalner)
# can't byte-fallback in their fast variants. CD-lxcf — every entry has
# an unambiguous ASCII (or empty) equivalent so we don't lose semantic
# information. Diacritics on Latin chars (é, ñ, ü) are NOT mapped:
# modern tokenizer vocabs cover them and stripping would lose meaning.
_REPLACEMENTS: dict[str, str] = {
    # Smart quotes → ASCII quotes
    "‘": "'",  # LEFT SINGLE QUOTATION MARK
    "’": "'",  # RIGHT SINGLE QUOTATION MARK / apostrophe
    "‚": "'",  # SINGLE LOW-9 QUOTATION MARK
    "‛": "'",  # SINGLE HIGH-REVERSED-9 QUOTATION MARK
    "“": '"',  # LEFT DOUBLE QUOTATION MARK
    "”": '"',  # RIGHT DOUBLE QUOTATION MARK
    "„": '"',  # DOUBLE LOW-9 QUOTATION MARK
    "‟": '"',  # DOUBLE HIGH-REVERSED-9 QUOTATION MARK
    "′": "'",  # PRIME
    "″": '"',  # DOUBLE PRIME
    # Dashes → ASCII hyphen / hyphen-minus
    "‐": "-",  # HYPHEN
    "‑": "-",  # NON-BREAKING HYPHEN
    "‒": "-",  # FIGURE DASH
    "–": "-",  # EN DASH
    "—": "-",  # EM DASH
    "―": "-",  # HORIZONTAL BAR
    "−": "-",  # MINUS SIGN
    # Whitespace variants → regular space (NFKC handles many already; this
    # backstops the ones it doesn't map).
    " ": " ",  # NO-BREAK SPACE
    " ": " ",  # EN QUAD
    " ": " ",  # EM QUAD
    " ": " ",  # EN SPACE
    " ": " ",  # EM SPACE
    " ": " ",  # THREE-PER-EM SPACE
    " ": " ",  # FOUR-PER-EM SPACE
    " ": " ",  # SIX-PER-EM SPACE
    " ": " ",  # FIGURE SPACE
    " ": " ",  # PUNCTUATION SPACE
    " ": " ",  # THIN SPACE
    " ": " ",  # HAIR SPACE
    " ": " ",  # NARROW NO-BREAK SPACE
    " ": " ",  # MEDIUM MATHEMATICAL SPACE
    "　": " ",  # IDEOGRAPHIC SPACE
    # Zero-width / formatting (drop entirely)
    "​": "",  # ZERO WIDTH SPACE
    "‌": "",  # ZERO WIDTH NON-JOINER
    "‍": "",  # ZERO WIDTH JOINER
    "﻿": "",  # BYTE ORDER MARK / ZERO WIDTH NO-BREAK SPACE
    "­": "",  # SOFT HYPHEN (rendering hint, not a real character)
    # Ellipsis → three ASCII dots
    "…": "...",
    # Bullets → ASCII asterisk
    "•": "*",  # BULLET
    "‣": "*",  # TRIANGULAR BULLET
    "◦": "*",  # WHITE BULLET
}


def normalize_text(text: str) -> str:
    """NFKC normalization + targeted ASCII map + control character strip.

    Order:
    1. NFKC compatibility-decompose (handles ligatures, fullwidth digits,
       superscripts, etc.) and recompose where the canonical form exists.
    2. Apply targeted ASCII replacements for characters NFKC leaves alone
       but that our SentencePiece-based tokenizers can't byte-fallback —
       smart quotes, em/en dashes, NBSP variants, soft hyphen, BOM,
       ellipsis, bullets.
    3. Strip null bytes and most control chars (preserves newline / tab).

    Applied at ingestion (chunking) so all downstream assets get clean text,
    and again at LLM boundary as a safety net. Idempotent.

    Diacritics (é, ñ, ü) are PRESERVED — modern tokenizer vocabs cover
    them and stripping would lose semantic information.
    """
    if not text:
        return text
    text = unicodedata.normalize("NFKC", text)
    if any(ch in _REPLACEMENTS for ch in text):
        text = "".join(_REPLACEMENTS.get(ch, ch) for ch in text)
    text = "".join(c for c in text if c >= " " or c in "\n\r\t")
    return text
