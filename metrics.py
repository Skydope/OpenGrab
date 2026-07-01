"""Prometheus metrics for OpenGrab.

All collectors are registered on the global REGISTRY. Use
``generate_latest()`` for the ``/metrics`` endpoint.

Conventions:
- ``opengrab_`` prefix on every metric
- Labels are low-cardinality by design (route patterns, not resolved paths)
- Durations use ``time.monotonic()`` (NTP-safe)
"""

from prometheus_client import (
    REGISTRY,
    Counter,
    Gauge,
    Histogram,
    Info,
    generate_latest,
)

download_total = Counter(
    "opengrab_download_total",
    "Total downloads completed by status",
    ["status", "extractor"],
)

download_duration = Histogram(
    "opengrab_download_duration_seconds",
    "Download duration in seconds",
    ["extractor", "quality"],
    buckets=(5, 10, 30, 60, 120, 300, 600, 1800, 3600),
)

download_bytes = Counter(
    "opengrab_download_bytes_total",
    "Total bytes downloaded",
    ["extractor"],
)

jobs_active = Gauge(
    "opengrab_jobs_active",
    "Currently active download jobs",
)

storage_bytes = Gauge(
    "opengrab_storage_bytes_used",
    "Current storage usage in bytes",
)

http_requests = Counter(
    "opengrab_http_requests_total",
    "Total HTTP requests handled",
    ["endpoint", "method", "status_code"],
)

ytdlp_version = Info(
    "opengrab_ytdlp_version",
    "yt-dlp version info",
)

__all__ = [
    "REGISTRY",
    "download_bytes",
    "download_duration",
    "download_total",
    "generate_latest",
    "http_requests",
    "jobs_active",
    "storage_bytes",
    "ytdlp_version",
]
