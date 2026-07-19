"""分段擷取與拼接（對應 SDD §7.2、§7.3）。

邏輯與 C1 spike 一致：每段以真實 viewport screenshot 為準，依 actual scrollY
與 PNG／CSS 比值裁切拼接，避免重複導覽列；第一段保留 fixed/sticky，後續段暫時隱藏。
"""

from __future__ import annotations

import io
import logging
import time
from pathlib import Path

from PIL import Image

from native_capture.page_prepare import hide_pinned_fixed_sticky, restore_hidden

logger = logging.getLogger("native_capture.stitcher")

MAX_SEGMENTS = 200


class StitchError(RuntimeError):
    """分段擷取或拼接無法證明完整性時拋出。"""


def capture_segments(
    driver,
    page_height_css: int,
    viewport_height: int,
    segments_dir: Path,
    settle_ms: int = 2000,
) -> tuple[list[dict], list[Image.Image], list[dict]]:
    """分段擷取 viewport screenshot；回傳 (segments 中繼資料, PIL Image 清單, fixed/sticky 記錄)。

    對應 SDD §7.2：每段記錄 requested/actual scrollY、inner viewport、PNG 尺寸；
    最後一段依 scrollHeight - innerHeight 的實際 scrollY 裁切，不補空白。
    """
    segments_dir.mkdir(parents=True, exist_ok=True)
    segments: list[dict] = []
    images: list[Image.Image] = []
    fixed_sticky_records: list[dict] = []
    max_scroll = max(0, page_height_css - viewport_height)
    scroll_y = 0
    index = 0

    while True:
        driver.execute_script(f"window.scrollTo(0, {scroll_y})")
        time.sleep(settle_ms / 1000.0)
        actual_scroll_y = driver.execute_script("return window.scrollY")
        inner = driver.execute_script("return [window.innerWidth, window.innerHeight]")

        if index == 0:
            # 第一段保留 fixed/sticky 元素（對應 SDD §7.2）。
            png_bytes = driver.get_screenshot_as_png()
        else:
            hidden = hide_pinned_fixed_sticky(driver)
            fixed_sticky_records.extend(hidden)
            png_bytes = driver.get_screenshot_as_png()
            restore_hidden(driver)

        img = Image.open(io.BytesIO(png_bytes))
        img.load()
        (segments_dir / f"segment-{index:04d}.png").write_bytes(png_bytes)

        segments.append(
            {
                "index": index,
                "requested_scroll_y": scroll_y,
                "actual_scroll_y": actual_scroll_y,
                "inner_width": inner[0],
                "inner_height": inner[1],
                "png_width": img.width,
                "png_height": img.height,
            }
        )
        images.append(img)
        logger.info(
            "segment %d：requested=%d actual=%d png=%dx%d",
            index, scroll_y, actual_scroll_y, img.width, img.height,
        )

        if actual_scroll_y >= max_scroll or index >= MAX_SEGMENTS:
            break
        index += 1
        scroll_y = min(scroll_y + viewport_height, max_scroll)

    return segments, images, fixed_sticky_records


def stitch(images: list[Image.Image], segments: list[dict], dpr: float, page_height_css: int) -> Image.Image:
    """依 actual scrollY 與 PNG/CSS 比值裁切拼接成整頁長圖，避免重複導覽列。

    對應 SDD §7.3：無法確認完整性時必須 fail，不得拼出看似成功的殘圖。
    """
    if not images:
        raise StitchError("無任何 segment 可供拼接")

    width = images[0].width
    final_height_px = round(page_height_css * dpr)
    canvas = Image.new("RGB", (width, final_height_px), "white")

    prev_bottom_px = 0
    for i, (img, seg) in enumerate(zip(images, segments)):
        y_px = round(seg["actual_scroll_y"] * dpr)
        if i == 0:
            paste_h = min(img.height, final_height_px)
            canvas.paste(img.crop((0, 0, width, paste_h)), (0, 0))
            prev_bottom_px = paste_h
            continue

        overlap_px = max(0, prev_bottom_px - y_px)
        crop_top = min(overlap_px, img.height)
        paste_y = y_px + crop_top
        paste_h = max(0, min(img.height - crop_top, final_height_px - paste_y))
        if paste_h > 0:
            canvas.paste(img.crop((0, crop_top, width, crop_top + paste_h)), (0, paste_y))
            prev_bottom_px = paste_y + paste_h

    return canvas


def verify_stitched_dimensions(
    full_page: Image.Image, inner_width: int, dpr: float, page_height_css: int
) -> None:
    """驗證拼接結果寬高與 innerWidth×DPR／document height×DPR 誤差在 1px 內，否則拋出 StitchError。

    對應 SDD §6 硬閘 7：容許 1px rounding。
    """
    expected_width = round(inner_width * dpr)
    if abs(full_page.width - expected_width) > 1:
        raise StitchError(
            f"最終 PNG 寬度 {full_page.width} 與 innerWidth×DPR={expected_width} 誤差超過 1px"
        )

    expected_height = round(page_height_css * dpr)
    if abs(full_page.height - expected_height) > 1:
        raise StitchError(
            f"最終 PNG 高度 {full_page.height} 與 document height×DPR={expected_height} 誤差超過 1px"
        )
