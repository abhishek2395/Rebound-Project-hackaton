"""
Base HTTP & Client Utilities
Provides standardized retry logic with exponential backoff, latency profiling,
and exception handling for external multi-app APIs.
"""

import logging
import time
from contextlib import contextmanager
from typing import Any, Callable, Dict, Generator, List, Optional, Tuple, TypeVar

from tenacity import (
    RetryError,
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

logger = logging.getLogger("rebound.clients")

T = TypeVar("T")


class ExternalAPIError(Exception):
    """Base exception for external API communication failures."""
    def __init__(self, message: str, status_code: Optional[int] = None, response_body: Optional[str] = None):
        super().__init__(message)
        self.status_code = status_code
        self.response_body = response_body


class RetryableAPIError(ExternalAPIError):
    """Raised on 5xx or 429 status codes where a retry with backoff is appropriate."""
    pass


class NonRetryableAPIError(ExternalAPIError):
    """Raised on 4xx status codes (e.g. 400 Bad Request, 404 Not Found) or business logic errors."""
    pass


@contextmanager
def measure_latency() -> Generator[Dict[str, int], None, None]:
    """Context manager that measures wall-clock execution time in milliseconds."""
    result: Dict[str, int] = {"ms": 0}
    start = time.perf_counter()
    try:
        yield result
    finally:
        result["ms"] = int((time.perf_counter() - start) * 1000)


def create_retry_decorator(max_attempts: int = 3, min_wait: float = 1.0, max_wait: float = 4.0):
    """Creates a tenacity retry decorator tuned for API backoff."""
    return retry(
        retry=retry_if_exception_type(RetryableAPIError),
        stop=stop_after_attempt(max_attempts),
        wait=wait_exponential(multiplier=min_wait, max=max_wait),
        reraise=True,
    )


class BaseClient:
    """Base class for all multi-app service clients (real and fakes)."""
    def __init__(self, dry_run: bool = False):
        self.dry_run = dry_run
        self.retry_counts: Dict[str, int] = {}

    def record_retry(self, operation: str) -> None:
        self.retry_counts[operation] = self.retry_counts.get(operation, 0) + 1

