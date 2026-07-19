"""native_capture.metrics 的測試：schema_version 1 骨架欄位、write_metrics 讀寫一致、
collect_headings 對 driver 回傳值的透傳與 None 防呆。
"""

from __future__ import annotations

import json
from pathlib import Path

from native_capture.metrics import collect_headings, new_metrics_skeleton, write_metrics


class TestNewMetricsSkeleton:
    def test_schema_version_is_1(self) -> None:
        metrics = new_metrics_skeleton("req-1", "https://example.com/")
        assert metrics["schema_version"] == 1

    def test_top_level_keys_present(self) -> None:
        metrics = new_metrics_skeleton("req-1", "https://example.com/")
        for key in ("schema_version", "request", "environment", "viewport", "document", "stitch", "headings"):
            assert key in metrics

    def test_request_sub_keys(self) -> None:
        metrics = new_metrics_skeleton("req-42", "https://example.com/page")
        assert metrics["request"]["request_id"] == "req-42"
        assert metrics["request"]["requested_url"] == "https://example.com/page"
        assert metrics["request"]["final_url"] is None
        assert "captured_at_utc" in metrics["request"]

    def test_stitch_sub_keys(self) -> None:
        metrics = new_metrics_skeleton("req-1", "https://example.com/")
        stitch = metrics["stitch"]
        assert stitch["status"] == "pending"
        assert stitch["segment_count"] == 0
        assert stitch["segments"] == []
        assert stitch["fixed_sticky_hidden"] == []
        assert stitch["final_png_width"] == 0
        assert stitch["final_png_height"] == 0
        assert stitch["warnings"] == []


class TestWriteMetricsRoundtrip:
    def test_write_then_read_back_matches(self, tmp_path: Path) -> None:
        metrics = new_metrics_skeleton("req-1", "https://example.com/")
        metrics["document"]["title"] = "測試頁面標題"
        path = write_metrics(metrics, tmp_path)
        assert path == tmp_path / "metrics.json"
        loaded = json.loads(path.read_text(encoding="utf-8"))
        assert loaded == metrics
        assert loaded["document"]["title"] == "測試頁面標題"


class _HeadingsFakeDriver:
    def __init__(self, result: object) -> None:
        self._result = result

    def execute_script(self, script: str, *args) -> object:
        return self._result


class TestCollectHeadings:
    def test_passes_through_driver_result(self) -> None:
        canned = [{"tag": "H1", "text": "標題", "selector": "body > h1"}]
        driver = _HeadingsFakeDriver(canned)
        assert collect_headings(driver) == canned

    def test_defaults_to_empty_list_when_none(self) -> None:
        driver = _HeadingsFakeDriver(None)
        assert collect_headings(driver) == []
