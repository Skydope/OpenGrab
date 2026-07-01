"""Tests for Prometheus metrics collectors and /metrics endpoint."""
from __future__ import annotations

import importlib

import pytest


@pytest.fixture(scope="module")
def _reset_metrics_module():
    """Reload metrics module once per module to get fresh collectors."""
    import metrics

    from prometheus_client import REGISTRY

    REGISTRY._collector_to_names.clear()
    REGISTRY._names_to_collectors.clear()
    importlib.reload(metrics)


class TestMetricsUnit:
    """Unit tests: counters, gauge, histogram. Each test imports fresh collectors."""

    def test_download_total_increments(self):
        from metrics import download_total

        before = download_total.labels(status="done", extractor="youtube")._value.get()
        download_total.labels(status="done", extractor="youtube").inc()
        download_total.labels(status="done", extractor="youtube").inc(2)
        after = download_total.labels(status="done", extractor="youtube")._value.get()
        assert after - before == 3.0

    def test_download_bytes_counter(self):
        from metrics import download_bytes

        before = download_bytes.labels(extractor="youtube")._value.get()
        download_bytes.labels(extractor="youtube").inc(1024)
        download_bytes.labels(extractor="youtube").inc(2048)
        after = download_bytes.labels(extractor="youtube")._value.get()
        assert after - before == 3072.0

    def test_http_requests_labels(self):
        from metrics import http_requests

        before = http_requests.labels(
            endpoint="/api/jobs/{id}/cancel", method="POST", status_code="200",
        )._value.get()
        http_requests.labels(
            endpoint="/api/jobs/{id}/cancel", method="POST", status_code="200",
        ).inc(2)
        after = http_requests.labels(
            endpoint="/api/jobs/{id}/cancel", method="POST", status_code="200",
        )._value.get()
        assert after - before == 2.0

    def test_jobs_active_track_inprogress(self):
        from metrics import jobs_active
        from prometheus_client import REGISTRY

        val = REGISTRY.get_sample_value("opengrab_jobs_active") or 0.0

        with jobs_active.track_inprogress():
            assert (REGISTRY.get_sample_value("opengrab_jobs_active") or 0.0) - val >= 0.0

        assert (REGISTRY.get_sample_value("opengrab_jobs_active") or 0.0) == val

    def test_jobs_active_exception_safety(self):
        from metrics import jobs_active
        from prometheus_client import REGISTRY

        val = REGISTRY.get_sample_value("opengrab_jobs_active") or 0.0

        try:
            with jobs_active.track_inprogress():
                raise RuntimeError("simulated")
        except RuntimeError:
            pass

        assert (REGISTRY.get_sample_value("opengrab_jobs_active") or 0.0) == val

    def test_storage_bytes_gauge(self):
        from metrics import storage_bytes

        storage_bytes.set(500)
        assert storage_bytes._value.get() == 500.0
        storage_bytes.set(0)
        assert storage_bytes._value.get() == 0.0

    def test_nested_track_inprogress(self):
        from metrics import jobs_active
        from prometheus_client import REGISTRY

        val = REGISTRY.get_sample_value("opengrab_jobs_active") or 0.0

        with jobs_active.track_inprogress():
            mid = REGISTRY.get_sample_value("opengrab_jobs_active") or 0.0
            assert mid - val == 1.0
            with jobs_active.track_inprogress():
                inner = REGISTRY.get_sample_value("opengrab_jobs_active") or 0.0
                assert inner - val == 2.0
            assert (REGISTRY.get_sample_value("opengrab_jobs_active") or 0.0) - val == 1.0
        assert (REGISTRY.get_sample_value("opengrab_jobs_active") or 0.0) == val

    def test_download_duration_buckets(self):
        from metrics import download_duration
        from prometheus_client import REGISTRY

        download_duration.labels(extractor="tiktok", quality="720p").observe(7)
        download_duration.labels(extractor="tiktok", quality="720p").observe(45)

        assert (REGISTRY.get_sample_value(
            "opengrab_download_duration_seconds_bucket",
            {"extractor": "tiktok", "quality": "720p", "le": "10.0"},
        ) or 0.0) == 1.0
        assert (REGISTRY.get_sample_value(
            "opengrab_download_duration_seconds_bucket",
            {"extractor": "tiktok", "quality": "720p", "le": "60.0"},
        ) or 0.0) == 2.0

    def test_generate_latest_output(self):
        from metrics import generate_latest, download_total

        download_total.labels(status="done", extractor="test").inc()
        text = generate_latest()
        output = text if isinstance(text, str) else text.decode()
        assert "opengrab_download_total" in output


class TestMetricsEndpoint:
    """Integration tests for the /metrics endpoint."""

    def test_metrics_requires_no_auth(self, client_no_auth):
        r = client_no_auth.get("/metrics")
        assert r.status_code == 200

    def test_metrics_returns_text_format(self, client):
        r = client.get("/metrics")
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("text/plain")
        assert "opengrab_" in r.text

    def test_metrics_endpoint_increments_http_counter(self, client):
        from prometheus_client import REGISTRY

        client.get("/metrics")
        sample = REGISTRY.get_sample_value(
            "opengrab_http_requests_total",
            {"endpoint": "/metrics", "method": "GET", "status_code": "200"},
        )
        assert sample is not None
        assert sample >= 1.0
