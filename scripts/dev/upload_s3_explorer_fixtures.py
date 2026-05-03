#!/usr/bin/env python3
"""Upload sample objects under ``dev/fixtures/`` so the S3 Explorer's
view-mode + media preview surfaces have something to render in dev (and
the Playwright tests for those modes don't auto-skip).

Usage::

    python scripts/dev/upload_s3_explorer_fixtures.py

Reads MinIO connection from the standard ``DAGSTER_S3_*`` env vars (matching
``task dev:viewer:api``). Idempotent — overwrites existing keys.
"""

from __future__ import annotations

import os
import struct
import wave
from io import BytesIO

from dagster_io.s3_client import S3Client

README_MD = """# S3 Explorer Fixture

This is a **markdown fixture** uploaded so the S3 Explorer can demo the
`Rendered | Raw` view-mode toggle for `.md` files.

## Sections

- Headings render at multiple levels
- _Italic_ and **bold** text
- Inline `code` and fenced blocks

```python
def hello():
    return "world"
```

## Lists

1. Ordered item one
2. Ordered item two
   - Nested unordered

> Blockquote — useful for QA assertions.

[Link to the viewer docs](http://localhost:8080/viewer/docs).
"""


def _png_1x1_red() -> bytes:
    """Hand-rolled valid 1x1 red PNG. ~70 bytes, no PIL dep."""
    return bytes.fromhex(
        "89504e470d0a1a0a"  # PNG magic
        "0000000d49484452"  # IHDR length=13, "IHDR"
        "00000001"  # width=1
        "00000001"  # height=1
        "0802000000"  # bit depth 8, RGB, default compression/filter/interlace
        "907753de"  # IHDR CRC
        "0000000c4944415408d76360f87f0000050001016f78da570000000049454e44ae426082"
    )


def _wav_silence(duration_seconds: float = 0.05, sample_rate: int = 8000) -> bytes:
    """Minimal valid WAV (mono 8 kHz silence). Browsers play it cleanly."""
    buf = BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)  # 16-bit
        w.setframerate(sample_rate)
        n = int(sample_rate * duration_seconds)
        w.writeframes(struct.pack(f"<{n}h", *([0] * n)))
    return buf.getvalue()


def _jsonl(rows: list[dict]) -> bytes:
    import json as _json

    return ("\n".join(_json.dumps(r) for r in rows) + "\n").encode("utf-8")


CONGRESS_DOCS = [
    {
        "id": "fixture-hr-1",
        "title": "H.R. 1 — For The People Act (fixture)",
        "source": "fixture",
        "source_path": "dev/fixtures/congress/hr-1.txt",
        "domain": "congress",
        "document_type": "bill",
        "metadata": {"chamber": "house", "congress_num": 117},
    },
    {
        "id": "fixture-s-1",
        "title": "S. 1 — Freedom to Vote Act (fixture)",
        "source": "fixture",
        "source_path": "dev/fixtures/congress/s-1.txt",
        "domain": "congress",
        "document_type": "bill",
        "metadata": {"chamber": "senate", "congress_num": 117},
    },
    {
        "id": "fixture-hr-3076",
        "title": "H.R. 3076 — Postal Service Reform (fixture)",
        "source": "fixture",
        "source_path": "dev/fixtures/congress/hr-3076.txt",
        "domain": "congress",
        "document_type": "bill",
        "metadata": {"chamber": "house", "congress_num": 117},
    },
]


LEAK_DOCS = [
    {
        "id": "fixture-leak-001",
        "title": "Internal memo — TestCorp (fixture)",
        "source": "fixture",
        "source_path": "dev/fixtures/leaks/memo-001.txt",
        "domain": "leaks",
        "document_type": "memo",
        "metadata": {"organization": "TestCorp", "leak_date": "2025-08-15"},
    },
    {
        "id": "fixture-leak-002",
        "title": "Quarterly briefing — TestCorp (fixture)",
        "source": "fixture",
        "source_path": "dev/fixtures/leaks/briefing-002.txt",
        "domain": "leaks",
        "document_type": "briefing",
        "metadata": {"organization": "TestCorp", "leak_date": "2025-09-02"},
    },
    {
        "id": "fixture-leak-003",
        "title": "Email thread — Acme Inc (fixture)",
        "source": "fixture",
        "source_path": "dev/fixtures/leaks/email-003.txt",
        "domain": "leaks",
        "document_type": "email",
        "metadata": {"organization": "Acme Inc", "leak_date": "2025-10-11"},
    },
]


def main() -> None:
    c = S3Client(
        endpoint_url=os.environ.get("DAGSTER_S3_ENDPOINT_URL", "http://localhost:9000"),
        access_key=os.environ.get("DAGSTER_S3_ACCESS_KEY", "minio"),
        secret_key=os.environ.get("DAGSTER_S3_SECRET_KEY", "minio123"),
        bucket=os.environ.get("DAGSTER_S3_BUCKET", "dagster"),
    )

    fixtures: list[tuple[str, bytes]] = [
        # S3 Explorer fixtures (markdown / image / audio view-modes)
        ("dev/fixtures/README.md", README_MD.encode("utf-8")),
        ("dev/fixtures/sample.png", _png_1x1_red()),
        ("dev/fixtures/sample.wav", _wav_silence()),
        # Documents-tab fixtures: silver/<code_location>/<group>/<asset>/data.jsonl
        # paths the DocumentsService reads from. Without these the congress
        # + leaks browsers render an empty list against the local seed.
        (
            "silver/congress_data/congress/congress_documents/data.jsonl",
            _jsonl(CONGRESS_DOCS),
        ),
        ("silver/open_leaks/leaks/leak_documents/data.jsonl", _jsonl(LEAK_DOCS)),
    ]
    for key, body in fixtures:
        c.put_object(key, body)
        print(f"uploaded {len(body):>5} bytes -> s3://{c.bucket}/{key}")


if __name__ == "__main__":
    main()
