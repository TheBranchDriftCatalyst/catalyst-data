#!/usr/bin/env python3
"""Upload sample objects under ``dev/fixtures/`` so the S3 Explorer's
view-mode toggles have something to render in dev (and the Playwright
markdown view-mode test doesn't auto-skip for lack of a ``.md`` key).

Usage::

    python scripts/dev/upload_s3_explorer_fixtures.py

Reads MinIO connection from the standard ``DAGSTER_S3_*`` env vars (matching
``task dev:viewer:api``). Idempotent — overwrites existing keys.
"""

from __future__ import annotations

import os

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


def main() -> None:
    c = S3Client(
        endpoint_url=os.environ.get("DAGSTER_S3_ENDPOINT_URL", "http://localhost:9000"),
        access_key=os.environ.get("DAGSTER_S3_ACCESS_KEY", "minio"),
        secret_key=os.environ.get("DAGSTER_S3_SECRET_KEY", "minio123"),
        bucket=os.environ.get("DAGSTER_S3_BUCKET", "dagster"),
    )
    body = README_MD.encode("utf-8")
    c.put_object("dev/fixtures/README.md", body)
    print(f"uploaded {len(body)} bytes -> s3://{c.bucket}/dev/fixtures/README.md")


if __name__ == "__main__":
    main()
