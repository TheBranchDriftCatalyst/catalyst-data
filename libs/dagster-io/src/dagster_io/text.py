"""Shared text utilities — normalization, cleaning.

Functions here are used at both ingestion (chunking) and LLM boundaries.
"""

from __future__ import annotations

import unicodedata


def normalize_text(text: str) -> str:
    """NFKC normalization + control character removal.

    - Converts fullwidth characters to ASCII equivalents (： → :, ｜ → |)
    - Decomposes ligatures (ﬁ → fi, ﬂ → fl)
    - Normalizes compatible Unicode forms
    - Strips null bytes and control chars (preserves newline/tab)

    Applied at ingestion (chunking) so all downstream assets get clean text,
    and again at LLM boundary as a safety net.
    """
    text = unicodedata.normalize("NFKC", text)
    text = "".join(c for c in text if c >= " " or c in "\n\r\t")
    return text
