"""命令列進入點：串接 safari_session、page_prepare、stitcher、metrics、preview_bundle。

行為與 C1 spike 的 main() 一致（同一組環境變數、同一組硬閘判斷），差異只在於改呼叫
正式模組化後的函式，並多產生 preview bundle。
"""

from __future__ import annotations

import logging
import os
import sys
import uuid
from pathlib import Path

from native_capture import metrics as metrics_mod
from native_capture import page_prepare
from native_capture import preview_bundle as preview_bundle_mod
from native_capture import safari_session
from native_capture import stitcher
from native_capture.url_validation import UrlValidationError, mask_url_for_log, validate_url

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("native_capture.cli")


def _env_int(name: str, default: int) -> int:
    return int(os.environ.get(name, str(default)))


def run(output_dir: Path | None = None) -> int:
    """執行一次完整長截圖流程，回傳 process exit code（0 成功，非 0 失敗）。

    失敗時保留 segments\\，metrics.json 標記 status=fail 並附警告訊息，
    對應 SDD 不變式：無法證明完整就 fail，不能用 warning 包裝成功。
    """
    request_id = os.environ.get("NATIVE_CAPTURE_REQUEST_ID") or str(uuid.uuid4())
    raw_url = os.environ.get("NATIVE_CAPTURE_URL", "https://zemeei.56855685.xyz/")
    viewport_width = _env_int("NATIVE_CAPTURE_VIEWPORT_WIDTH", 1136)
    viewport_height = _env_int("NATIVE_CAPTURE_VIEWPORT_HEIGHT", 1129)
    settle_ms = _env_int("NATIVE_CAPTURE_SETTLE_MS", 2000)
    output_dir = output_dir or Path(os.environ.get("NATIVE_CAPTURE_OUTPUT_DIR", "capture-output"))
    segments_dir = output_dir / "segments"
    preview_dir = output_dir / "preview"

    metrics = metrics_mod.new_metrics_skeleton(request_id, raw_url)

    try:
        validate_url(raw_url)
    except UrlValidationError as exc:
        return _fail(f"URL 驗證失敗：{exc}", metrics, output_dir)

    logger.info("目標網址（已遮罩）：%s", mask_url_for_log(raw_url))

    driver = safari_session.create_driver()
    try:
        caps = safari_session.assert_safari_capabilities(driver)
        metrics["environment"]["capabilities"] = caps

        driver.get(raw_url)
        fonts_status = page_prepare.wait_ready(driver)
        metrics["request"]["final_url"] = driver.current_url

        platform = safari_session.assert_macos_platform(driver)
        metrics["environment"]["platform"] = platform
        metrics["environment"].update(safari_session.collect_environment_basics(driver))

        inner = page_prepare.correct_viewport(driver, viewport_width, viewport_height)

        metrics["document"]["html_class_name"] = driver.execute_script(
            "return document.documentElement.className"
        )
        is_macos = driver.execute_script(
            "return document.documentElement.classList.contains('is-macos')"
        )
        if not is_macos:
            logger.info("document.documentElement.classList 不含 is-macos（非致命，僅記錄）")

        dpr = driver.execute_script("return window.devicePixelRatio")
        metrics["environment"]["device_pixel_ratio"] = dpr
        if dpr < 1:
            return _fail(f"window.devicePixelRatio 異常：{dpr}", metrics, output_dir)

        page_prepare.freeze_dynamics(driver)
        page_height_css = page_prepare.prescroll_until_stable(driver, viewport_height)
        metrics["document"].update(
            {
                "title": driver.execute_script("return document.title"),
                "scroll_width": driver.execute_script("return document.documentElement.scrollWidth"),
                "scroll_height": page_height_css,
                "ready_state": "complete",
                "fonts_status": fonts_status,
            }
        )
        metrics["viewport"] = {"inner_width": inner[0], "inner_height": inner[1]}

        segments, images, fixed_sticky_records = stitcher.capture_segments(
            driver, page_height_css, viewport_height, segments_dir, settle_ms
        )
        metrics["stitch"]["segments"] = segments
        metrics["stitch"]["segment_count"] = len(segments)
        metrics["stitch"]["fixed_sticky_hidden"] = fixed_sticky_records

        full_page = stitcher.stitch(images, segments, dpr, page_height_css)
        stitcher.verify_stitched_dimensions(full_page, inner[0], dpr, page_height_css)

        output_dir.mkdir(parents=True, exist_ok=True)
        full_page_path = output_dir / "safari-full-page.png"
        full_page.save(full_page_path)

        metrics["stitch"]["final_png_width"] = full_page.width
        metrics["stitch"]["final_png_height"] = full_page.height
        metrics["stitch"]["status"] = "pass"
        metrics["headings"] = metrics_mod.collect_headings(driver)

        preview_bundle_mod.generate_preview_bundle(full_page_path, preview_dir, output_dir)

    except Exception as exc:  # noqa: BLE001 -- 任何硬閘失敗都要走同一條 fail-closed 路徑。
        return _fail(str(exc), metrics, output_dir)
    finally:
        driver.quit()

    metrics_mod.write_metrics(metrics, output_dir)
    logger.info(
        "擷取成功：full-page=%dx%d segments=%d",
        full_page.width, full_page.height, len(segments),
    )
    return 0


def _fail(reason: str, metrics: dict, output_dir: Path) -> int:
    """記錄硬閘失敗原因，落 metrics.json（status=fail），回傳非零 exit code。"""
    logger.error("硬閘失敗：%s", reason)
    metrics.setdefault("stitch", {})["status"] = "fail"
    metrics.setdefault("stitch", {}).setdefault("warnings", []).append(reason)
    metrics_mod.write_metrics(metrics, output_dir)
    return 1


def main() -> None:
    sys.exit(run())


if __name__ == "__main__":
    main()
