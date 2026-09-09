"""分段擷取與拼接（對應 SDD §7.2、§7.3）。

邏輯與 C1 spike 一致：每段以真實 viewport screenshot 為準，依 actual scrollY
與 PNG／CSS 比值裁切拼接，避免重複導覽列；第一段保留 fixed/sticky，後續段暫時隱藏。
"""

from __future__ import annotations

import io
import logging
import math
import time
from pathlib import Path

from PIL import Image

from native_capture.page_prepare import hide_pinned_fixed_sticky, restore_hidden

logger = logging.getLogger("native_capture.stitcher")

MAX_SEGMENTS = 200


class StitchError(RuntimeError):
    """分段擷取或拼接無法證明完整性時拋出。"""


def _validate_segment_inputs(
    images: list[Image.Image], segments: list[dict], dpr: float, page_height_css: int
) -> None:
    """在建立輸出 canvas 前驗證所有 segment 的尺寸與頁面覆蓋。"""
    if len(images) != len(segments):
        raise StitchError(f"images 與 segments 數量不一致：{len(images)} != {len(segments)}")
    if not images:
        raise StitchError("無任何 segment 可供拼接")
    if isinstance(dpr, bool) or not isinstance(dpr, (int, float)) or not math.isfinite(dpr) or dpr < 1:
        raise StitchError(f"DPR 無效：{dpr}")
    if isinstance(page_height_css, bool) or not isinstance(page_height_css, (int, float)):
        raise StitchError(f"頁高無效：{page_height_css}")
    if page_height_css <= 0:
        raise StitchError(f"頁高必須是正數：{page_height_css}")

    first = segments[0]
    try:
        expected_inner_width = first["inner_width"]
    except (KeyError, TypeError) as exc:
        raise StitchError("segment 缺少 viewport 尺寸資料") from exc
    if expected_inner_width <= 0:
        raise StitchError(f"segment inner_width 無效：{expected_inner_width}")
    expected_width_px = round(expected_inner_width * dpr)
    previous_actual_y: float | None = None
    previous_coverage_end_px = 0
    final_height_px = round(page_height_css * dpr)

    for index, (image, segment) in enumerate(zip(images, segments)):
        try:
            actual_y = segment["actual_scroll_y"]
            inner_width = segment["inner_width"]
            inner_height = segment["inner_height"]
            png_width = segment["png_width"]
            png_height = segment["png_height"]
        except (KeyError, TypeError) as exc:
            raise StitchError(f"segment {index} 缺少尺寸或 actual_scroll_y 資料") from exc

        values = (actual_y, inner_width, inner_height, png_width, png_height)
        if any(isinstance(value, bool) or not isinstance(value, (int, float)) for value in values):
            raise StitchError(f"segment {index} 尺寸或 actual_scroll_y 不是數字")
        if not all(math.isfinite(float(value)) for value in values):
            raise StitchError(f"segment {index} 尺寸或 actual_scroll_y 不是有限數字")
        if inner_width != expected_inner_width or inner_width <= 0 or inner_height <= 0:
            raise StitchError(f"segment {index} viewport 尺寸不一致")
        if png_width <= 0 or png_height <= 0 or image.size != (png_width, png_height):
            raise StitchError(f"segment {index} 宣告尺寸與實際圖片不一致")
        if png_width != expected_width_px or png_height != round(inner_height * dpr):
            raise StitchError(f"segment {index} 像素比例或尺寸不一致")

        if index == 0:
            if actual_y < -1 or actual_y > 1:
                raise StitchError(f"首段未從頁首開始：actual_scroll_y={actual_y}")
        elif actual_y <= previous_actual_y:
            raise StitchError(f"segment {index} scrollY 未前進：{actual_y} <= {previous_actual_y}")

        start_px = round(actual_y * dpr)
        end_px = start_px + png_height
        if start_px < -1 or start_px > final_height_px + 1:
            raise StitchError(f"segment {index} scrollY 超出頁面範圍：{actual_y}")
        if index > 0 and start_px > previous_coverage_end_px + 1:
            raise StitchError(
                f"segment {index} 與前段之間有缺口：{previous_coverage_end_px}px -> {start_px}px"
            )
        previous_actual_y = float(actual_y)
        previous_coverage_end_px = max(previous_coverage_end_px, end_px)

    if previous_coverage_end_px < final_height_px - 1:
        raise StitchError(
            f"最後一段未覆蓋頁尾：{previous_coverage_end_px}px < {final_height_px}px"
        )


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
    previous_actual_y: float | None = None
    index = 0

    while True:
        observed_height = driver.execute_script("return document.documentElement.scrollHeight")
        if observed_height != page_height_css:
            raise StitchError(
                f"擷取前頁高改變：預期 {page_height_css}px，實際 {observed_height}px"
            )
        driver.execute_script(f"window.scrollTo(0, {scroll_y})")
        time.sleep(settle_ms / 1000.0)
        actual_scroll_y = driver.execute_script("return window.scrollY")
        if (
            not isinstance(actual_scroll_y, (int, float))
            or isinstance(actual_scroll_y, bool)
            or not math.isfinite(float(actual_scroll_y))
        ):
            raise StitchError(f"segment {index} actual_scroll_y 無效：{actual_scroll_y}")
        if actual_scroll_y < -1 or actual_scroll_y > max_scroll + 1:
            raise StitchError(f"segment {index} actual_scroll_y 超出頁面範圍：{actual_scroll_y}")
        if index == 0 and actual_scroll_y > 1:
            raise StitchError(f"首段未從頁首開始：actual_scroll_y={actual_scroll_y}")
        if previous_actual_y is not None and actual_scroll_y <= previous_actual_y:
            raise StitchError(f"捲動停滯：segment {index} actual_scroll_y={actual_scroll_y}")
        inner = driver.execute_script("return [window.innerWidth, window.innerHeight]")

        if index == 0:
            # 第一段保留 fixed/sticky 元素（對應 SDD §7.2）。
            png_bytes = driver.get_screenshot_as_png()
        else:
            hidden = hide_pinned_fixed_sticky(driver)
            fixed_sticky_records.extend(hidden)
            try:
                png_bytes = driver.get_screenshot_as_png()
            finally:
                restore_hidden(driver)

        img = Image.open(io.BytesIO(png_bytes))
        img.load()
        (segments_dir / f"segment-{index:04d}.png").write_bytes(png_bytes)

        observed_height_after = driver.execute_script(
            "return document.documentElement.scrollHeight"
        )
        if observed_height_after != page_height_css:
            raise StitchError(
                f"擷取期間頁高改變：預期 {page_height_css}px，實際 {observed_height_after}px"
            )

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
        previous_actual_y = float(actual_scroll_y)
        logger.info(
            "segment %d：requested=%d actual=%d png=%dx%d",
            index, scroll_y, actual_scroll_y, img.width, img.height,
        )

        if actual_scroll_y >= max_scroll - 1:
            break
        if len(segments) >= MAX_SEGMENTS:
            raise StitchError(f"已達 segment 上限 {MAX_SEGMENTS} 但仍未到底")
        index += 1
        scroll_y = min(scroll_y + viewport_height, max_scroll)

    return segments, images, fixed_sticky_records


def stitch(images: list[Image.Image], segments: list[dict], dpr: float, page_height_css: int) -> Image.Image:
    """依 actual scrollY 與 PNG/CSS 比值裁切拼接成整頁長圖，避免重複導覽列。

    對應 SDD §7.3：無法確認完整性時必須 fail，不得拼出看似成功的殘圖。
    """
    _validate_segment_inputs(images, segments, dpr, page_height_css)

    width = images[0].width
    final_height_px = round(page_height_css * dpr)
    canvas = Image.new("RGB", (width, final_height_px), "white")

    previous_coverage_end_px = 0
    for i, (img, seg) in enumerate(zip(images, segments)):
        y_px = round(seg["actual_scroll_y"] * dpr)
        if i == 0:
            paste_h = min(img.height, final_height_px)
            canvas.paste(img.crop((0, 0, width, paste_h)), (0, 0))
            previous_coverage_end_px = img.height
            continue

        overlap_px = max(0, previous_coverage_end_px - y_px)
        crop_top = min(overlap_px, img.height)
        paste_y = y_px + crop_top
        paste_h = max(0, min(img.height - crop_top, final_height_px - paste_y))
        if paste_h > 0:
            canvas.paste(img.crop((0, crop_top, width, crop_top + paste_h)), (0, paste_y))
        previous_coverage_end_px = max(previous_coverage_end_px, y_px + img.height)

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
