"""Base API client with rate limiting, pagination, and retry logic.

This abstraction will move to a shared Python utility library.
congress-data serves as the reference implementation.
"""

from __future__ import annotations

import time
from abc import abstractmethod
from typing import Any, Iterator

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from dagster_io.logging import get_logger

logger = get_logger(__name__)


class BaseAPIClient:
    """HTTP API client with token-bucket rate limiting, retry, and pagination."""

    def __init__(
        self,
        api_key: str,
        requests_per_hour: int = 5000,
        timeout: float = 30.0,
        max_retries: int = 3,
    ):
        self.api_key = api_key
        self.timeout = timeout
        self._requests_per_hour = requests_per_hour
        self._min_interval = 3600.0 / requests_per_hour
        self._last_request_time: float = 0.0
        self._session: requests.Session | None = None
        self._max_retries = max_retries

    @property
    @abstractmethod
    def base_url(self) -> str: ...

    @property
    @abstractmethod
    def default_headers(self) -> dict[str, str]: ...

    # -- Context manager --

    def __enter__(self) -> BaseAPIClient:
        self._session = requests.Session()
        self._session.headers.update(self.default_headers)

        retry = Retry(
            total=self._max_retries,
            backoff_factor=1.0,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["GET"],
        )
        adapter = HTTPAdapter(max_retries=retry)
        self._session.mount("https://", adapter)
        self._session.mount("http://", adapter)
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        if self._session:
            self._session.close()
            self._session = None

    # -- Rate limiting --

    def _wait_for_rate_limit(self) -> None:
        now = time.monotonic()
        elapsed = now - self._last_request_time
        if elapsed < self._min_interval:
            wait = self._min_interval - elapsed
            logger.debug("Rate limit: waiting %.2fs", wait)
            time.sleep(wait)
        self._last_request_time = time.monotonic()

    # -- HTTP --

    def get(self, endpoint: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """Make a GET request with rate limiting."""
        if self._session is None:
            raise RuntimeError("Client must be used as a context manager")

        self._wait_for_rate_limit()

        url = f"{self.base_url.rstrip('/')}/{endpoint.lstrip('/')}"
        logger.debug("GET %s params=%s", url, params)

        resp = self._session.get(url, params=params or {}, timeout=self.timeout)
        resp.raise_for_status()
        logger.info("API GET %s status=%d size=%d", url, resp.status_code, len(resp.content))
        return resp.json()

    # -- Pagination --

    def paginate(
        self,
        endpoint: str,
        results_key: str,
        params: dict[str, Any] | None = None,
        page_size: int = 250,
        max_items: int | None = None,
    ) -> Iterator[dict[str, Any]]:
        """Paginate through an API endpoint yielding individual items."""
        params = dict(params or {})
        params["limit"] = page_size
        offset = params.pop("offset", 0)
        total_yielded = 0

        while True:
            params["offset"] = offset
            data = self.get(endpoint, params)
            items = data.get(results_key, [])

            if not items:
                break

            for item in items:
                yield item
                total_yielded += 1
                if max_items and total_yielded >= max_items:
                    return

            # Check for next page via pagination object or item count
            pagination = data.get("pagination", {})
            next_url = pagination.get("next")
            if not next_url or len(items) < page_size:
                break

            offset += page_size
