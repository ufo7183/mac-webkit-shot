"""CLI 高層 seam：成功產物與各類失敗的 metrics／exit code。"""

from __future__ import annotations

import io
import json
from pathlib import Path

import pytest
from PIL import Image

from native_capture import cli, page_prepare, preview_bundle, safari_session
from native_capture.stitcher import StitchError


def _png_bytes(width: int, height: int, color: tuple[int, int, int]) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), color).save(buffer, format="PNG")
    return buffer.getvalue()


class _CliDriver:
    """只模擬外部 WebDriver 邊界，核心流程使用 production 函式。"""

    capabilities = {
        "browserName": "safari",
        "browserVersion": "18.6",
        "safari:platformVersion": "macOS 15.6",
        "safari:driverVersion": "safaridriver 18.6",
        "platformName": "mac",
    }

    def __init__(self, screenshot_width: int = 8, quit_error: Exception | None = None) -> None:
        self.current_url = "https://fixture.example/complete"
        self._scroll_y = 0
        self._screenshot_width = screenshot_width
        self.quit_error = quit_error
        self.quit_called = False

    def get(self, url: str) -> None:
        self.current_url = url

    def set_window_rect(self, **kwargs: int) -> None:
        return None

    def execute_script(self, script: str, *args: object) -> object:
        if "document.readyState" in script:
            return "complete"
        if "navigator.platform" in script:
            return "MacIntel"
        if "navigator.userAgent" in script:
            return "Mozilla/5.0 Safari/18.6"
        if "navigator.maxTouchPoints" in script:
            return 0
        if "window.innerWidth, window.innerHeight" in script:
            return [8, 4]
        if "window.outerWidth" in script or "visualViewport" in script:
            return {
                "inner_width": 8,
                "inner_height": 4,
                "outer_width": 8,
                "outer_height": 4,
                "visual_viewport": {"width": 8, "height": 4, "scale": 1},
                "screen": {"width": 8, "height": 4},
            }
        if "document.documentElement.className" in script:
            return "fixture-page"
        if "classList.contains" in script:
            return False
        if "window.devicePixelRatio" in script:
            return 1
        if "document.title" in script:
            return "Fixture page"
        if "document.documentElement.scrollWidth" in script:
            return 8
        if "document.body.scrollHeight" in script:
            return 10
        if "document.documentElement.scrollHeight" in script:
            return 10
        if "window.scrollTo" in script:
            self._scroll_y = int(script.rsplit(",", 1)[1].rstrip("); "))
            return None
        if "window.scrollY" in script:
            return self._scroll_y
        if "data-native-capture-hidden" in script:
            return []
        if "querySelectorAll" in script:
            return []
        return None

    def execute_async_script(self, script: str, *args: object) -> str:
        assert "document.fonts.ready" in script
        return "loaded"

    def set_script_timeout(self, timeout_s: float) -> None:
        assert timeout_s > 0

    def get_screenshot_as_png(self) -> bytes:
        color = (255, 0, 0) if self._scroll_y == 0 else (0, 0, 255)
        return _png_bytes(self._screenshot_width, 4, color)

    def quit(self) -> None:
        self.quit_called = True
        if self.quit_error is not None:
            raise self.quit_error


def _set_fixture_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NATIVE_CAPTURE_URL", "https://fixture.example/complete")
    monkeypatch.setenv("NATIVE_CAPTURE_VIEWPORT_WIDTH", "8")
    monkeypatch.setenv("NATIVE_CAPTURE_VIEWPORT_HEIGHT", "4")
    monkeypatch.setenv("NATIVE_CAPTURE_SETTLE_MS", "0")


def _read_metrics(output_dir: Path) -> dict:
    return json.loads((output_dir / "metrics.json").read_text(encoding="utf-8"))


def test_run_success_creates_full_viewport_metrics_and_valid_preview(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _set_fixture_env(monkeypatch)
    driver = _CliDriver()
    monkeypatch.setattr(safari_session, "create_driver", lambda: driver)
    monkeypatch.setattr(page_prepare, "wait_ready", lambda _driver: "loaded")

    assert cli.run(tmp_path) == 0

    metrics = _read_metrics(tmp_path)
    assert metrics["stitch"]["status"] == "pass"
    assert metrics["document"]["fonts_status"] == "loaded"
    assert metrics["stitch"]["segment_count"] == 3
    assert metrics["environment"]["safari_version"] == "18.6"
    assert metrics["environment"]["macos_version"] == "macOS 15.6"
    assert (tmp_path / "safari-full-page.png").is_file()
    assert (tmp_path / "safari-first-viewport.png").is_file()
    manifest = preview_bundle.validate_preview_bundle(tmp_path, tmp_path / "preview")
    assert manifest["source"]["file"] == "safari-full-page.png"
    assert driver.quit_called


def test_run_driver_creation_failure_writes_failure_metrics(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _set_fixture_env(monkeypatch)
    monkeypatch.setattr(
        safari_session,
        "create_driver",
        lambda: (_ for _ in ()).throw(safari_session.SafariSessionError("Safari unavailable")),
    )

    assert cli.run(tmp_path) != 0
    metrics = _read_metrics(tmp_path)
    assert metrics["stitch"]["status"] == "fail"
    assert "Safari unavailable" in metrics["stitch"]["warnings"][0]


def test_run_font_failure_writes_failure_metrics_and_quits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _set_fixture_env(monkeypatch)
    driver = _CliDriver()
    monkeypatch.setattr(safari_session, "create_driver", lambda: driver)

    def fail_fonts(_driver: object) -> str:
        raise page_prepare.PagePrepareError("fonts.ready timeout", fonts_status="timeout")

    monkeypatch.setattr(page_prepare, "wait_ready", fail_fonts)

    assert cli.run(tmp_path) != 0
    metrics = _read_metrics(tmp_path)
    assert metrics["document"]["fonts_status"] == "timeout"
    assert metrics["stitch"]["status"] == "fail"
    assert driver.quit_called


def test_run_stitch_failure_is_not_reported_as_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _set_fixture_env(monkeypatch)
    driver = _CliDriver(screenshot_width=7)
    monkeypatch.setattr(safari_session, "create_driver", lambda: driver)
    monkeypatch.setattr(page_prepare, "wait_ready", lambda _driver: "loaded")

    assert cli.run(tmp_path) != 0
    metrics = _read_metrics(tmp_path)
    assert metrics["stitch"]["status"] == "fail"
    assert "比例" in metrics["stitch"]["warnings"][0]


def test_run_preview_failure_is_not_reported_as_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _set_fixture_env(monkeypatch)
    driver = _CliDriver()
    monkeypatch.setattr(safari_session, "create_driver", lambda: driver)
    monkeypatch.setattr(page_prepare, "wait_ready", lambda _driver: "loaded")
    monkeypatch.setattr(
        preview_bundle,
        "generate_preview_bundle",
        lambda *_args: (_ for _ in ()).throw(preview_bundle.PreviewBundleError("preview fixture failure")),
    )

    assert cli.run(tmp_path) != 0
    metrics = _read_metrics(tmp_path)
    assert metrics["stitch"]["status"] == "fail"
    assert "preview fixture failure" in metrics["stitch"]["warnings"][0]


def test_run_quit_failure_preserves_success_path_as_primary_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _set_fixture_env(monkeypatch)
    driver = _CliDriver(quit_error=RuntimeError("quit fixture failure"))
    monkeypatch.setattr(safari_session, "create_driver", lambda: driver)
    monkeypatch.setattr(page_prepare, "wait_ready", lambda _driver: "loaded")

    assert cli.run(tmp_path) != 0
    metrics = _read_metrics(tmp_path)
    assert metrics["stitch"]["status"] == "fail"
    assert any("quit fixture failure" in warning for warning in metrics["stitch"]["warnings"])
