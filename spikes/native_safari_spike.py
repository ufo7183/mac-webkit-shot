#!/usr/bin/env python3
"""Mac Safari 原生長截圖 C1 生死閘門 Spike。

驗證項目對應 SDD-mac-safari-capture.md §6 九個硬閘：
1. safaridriver 已於 workflow 中無互動啟用（此腳本假設外部已執行 --enable）。
2. webdriver.Safari() 建立 session 成功，capabilities.browserName 為 safari。
3. navigator.platform／capabilities 顯示為 macOS 環境。
4. document.documentElement.classList 含 is-macos。
5. window.devicePixelRatio 等於 2；不是 2 時仍記錄實際值，不假裝等同 Retina。
6. 頁面可從頂到最底完整分段並拼出 safari-full-page.png。
7. 最終 PNG 寬度等於 innerWidth × DPR（容許 1px rounding）。
8. 無明顯接縫、無重複導覽列，最後一段含頁尾。
9. driver.quit() 正常結束、不逾時。

輸出至 spike-output/：
- safari-full-page.png（正式產物）
- safari-first-viewport.png（診斷）
- metrics.json（環境／viewport／document／stitch 摘要）
- segments/（拼接過程的原始分段，供除錯）

此腳本是最小可行 spike，不是正式長截圖引擎（正式引擎在 C2 的 native_capture/ 套件實作，
會重用本腳本已驗證過的 API，不重寫另一套邏輯）。
"""

import io
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image
from selenium import webdriver
from selenium.webdriver.safari.options import Options as SafariOptions

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("native_safari_spike")

TARGET_URL = os.environ.get("SPIKE_TARGET_URL", "https://zemeei.56855685.xyz/")
VIEWPORT_WIDTH = int(os.environ.get("SPIKE_VIEWPORT_WIDTH", "1136"))
VIEWPORT_HEIGHT = int(os.environ.get("SPIKE_VIEWPORT_HEIGHT", "1129"))
SETTLE_MS = int(os.environ.get("SPIKE_SETTLE_MS", "2000"))
MAX_PAGE_HEIGHT_CSS_PX = 60000
MAX_SEGMENTS = 200

OUTPUT_DIR = Path(os.environ.get("SPIKE_OUTPUT_DIR", "spike-output"))
SEGMENTS_DIR = OUTPUT_DIR / "segments"


def fail(reason: str, metrics: dict | None = None) -> None:
    """記錄硬閘失敗原因，若已有 metrics 骨架則落檔後以非零 exit 結束。"""
    logger.error("硬閘失敗：%s", reason)
    if metrics is not None:
        metrics.setdefault("stitch", {})["status"] = "fail"
        metrics.setdefault("stitch", {}).setdefault("warnings", []).append(reason)
        write_metrics(metrics)
    sys.exit(1)


def write_metrics(metrics: dict) -> None:
    """寫出 metrics.json。"""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUTPUT_DIR / "metrics.json").write_text(
        json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def wait_ready(driver) -> str:
    """等待 document.readyState 與 document.fonts.ready，回傳字型狀態。"""
    deadline = time.time() + 30
    state = None
    while time.time() < deadline:
        state = driver.execute_script("return document.readyState")
        if state == "complete":
            break
        time.sleep(0.2)
    if state != "complete":
        fail(f"document.readyState 30 秒內未到 complete（最後狀態：{state}）")

    fonts_status = driver.execute_script(
        """
        var callback = arguments[arguments.length - 1];
        if (!document.fonts) { callback('unsupported'); return; }
        var timer = setTimeout(function () { callback('timeout'); }, 8000);
        document.fonts.ready.then(function () {
            clearTimeout(timer);
            callback('loaded');
        });
        """
    )
    logger.info("document.fonts.ready 狀態：%s", fonts_status)
    return fonts_status


def correct_viewport(driver, target_w: int, target_h: int) -> list:
    """校正 outer window，直到 inner viewport 與要求值誤差 <=1 CSS px，回傳最終 inner viewport。"""
    outer_w, outer_h = target_w, target_h
    inner = [0, 0]
    for attempt in range(6):
        driver.set_window_rect(x=0, y=0, width=outer_w, height=outer_h)
        inner = driver.execute_script("return [window.innerWidth, window.innerHeight]")
        diff_w = target_w - inner[0]
        diff_h = target_h - inner[1]
        logger.info("校正第 %d 輪：inner=%s 誤差=(%d,%d)", attempt + 1, inner, diff_w, diff_h)
        if abs(diff_w) <= 1 and abs(diff_h) <= 1:
            return inner
        outer_w += diff_w
        outer_h += diff_h
    fail(f"outer window 校正 6 輪後 inner viewport 仍未落在 ±1px（目標 {target_w}x{target_h}，實際 {inner}）")
    return inner


def prescroll_until_stable(driver) -> int:
    """從頁首逐段預捲到底觸發 lazy-load，最多 3 輪，連續兩輪 scrollHeight 相同才算穩定。"""
    heights = []
    for round_idx in range(3):
        driver.execute_script("window.scrollTo(0, 0)")
        time.sleep(0.3)
        height = driver.execute_script("return document.body.scrollHeight")
        pos = 0
        while pos < height:
            pos += VIEWPORT_HEIGHT
            driver.execute_script(f"window.scrollTo(0, {pos})")
            time.sleep(0.4)
            height = driver.execute_script("return document.body.scrollHeight")
        driver.execute_script("window.scrollTo(0, 0)")
        time.sleep(0.3)
        stable_height = driver.execute_script("return document.documentElement.scrollHeight")
        heights.append(stable_height)
        logger.info("預捲第 %d 輪 scrollHeight=%d", round_idx + 1, stable_height)
        if len(heights) >= 2 and heights[-1] == heights[-2]:
            if stable_height > MAX_PAGE_HEIGHT_CSS_PX:
                fail(f"頁面高度 {stable_height}px 超過上限 {MAX_PAGE_HEIGHT_CSS_PX}px")
            return stable_height
    fail(f"預捲 3 輪後 scrollHeight 仍不穩定：{heights}")
    return heights[-1] if heights else 0


def freeze_dynamics(driver) -> None:
    """凍結 CSS animation／transition／caret／smooth scroll；只改變動態時間，不改文字或排版。"""
    driver.execute_script(
        """
        var style = document.createElement('style');
        style.id = '__spike_freeze__';
        style.textContent =
            '*, *::before, *::after { animation-play-state: paused !important; ' +
            'transition: none !important; caret-color: transparent !important; } ' +
            'html { scroll-behavior: auto !important; }';
        document.head.appendChild(style);
        """
    )


def hide_pinned_fixed_sticky(driver) -> list:
    """暫時以 visibility:hidden 隱藏當下 pinned 的 fixed/sticky 元素，保留 layout footprint。"""
    hidden = driver.execute_script(
        """
        var results = [];
        var all = document.querySelectorAll('body *');
        for (var i = 0; i < all.length; i++) {
            var el = all[i];
            var cs = window.getComputedStyle(el);
            if ((cs.position === 'fixed' || cs.position === 'sticky') && cs.visibility !== 'hidden') {
                var rect = el.getBoundingClientRect();
                if (rect.top < window.innerHeight && rect.bottom > 0) {
                    el.setAttribute('data-spike-hidden', '1');
                    el.style.setProperty('visibility', 'hidden', 'important');
                    results.push({
                        tag: el.tagName,
                        id: el.id || null,
                        className: (el.className && el.className.toString) ? el.className.toString() : null,
                        position: cs.position
                    });
                }
            }
        }
        return results;
        """
    )
    return hidden or []


def restore_hidden(driver) -> None:
    """還原被暫時隱藏的 fixed/sticky 元素。"""
    driver.execute_script(
        """
        var hidden = document.querySelectorAll('[data-spike-hidden="1"]');
        for (var i = 0; i < hidden.length; i++) {
            hidden[i].style.removeProperty('visibility');
            hidden[i].removeAttribute('data-spike-hidden');
        }
        """
    )


def capture_segments(driver, page_height_css: int) -> tuple:
    """分段擷取 viewport screenshot；回傳 (segments 中繼資料, PIL Image 清單, fixed/sticky 記錄)。"""
    SEGMENTS_DIR.mkdir(parents=True, exist_ok=True)
    segments = []
    images = []
    fixed_sticky_records = []
    max_scroll = max(0, page_height_css - VIEWPORT_HEIGHT)
    scroll_y = 0
    index = 0

    while True:
        driver.execute_script(f"window.scrollTo(0, {scroll_y})")
        time.sleep(SETTLE_MS / 1000.0)
        actual_scroll_y = driver.execute_script("return window.scrollY")
        inner = driver.execute_script("return [window.innerWidth, window.innerHeight]")

        if index == 0:
            png_bytes = driver.get_screenshot_as_png()
        else:
            hidden = hide_pinned_fixed_sticky(driver)
            fixed_sticky_records.extend(hidden)
            png_bytes = driver.get_screenshot_as_png()
            restore_hidden(driver)

        img = Image.open(io.BytesIO(png_bytes))
        img.load()
        (SEGMENTS_DIR / f"segment-{index:04d}.png").write_bytes(png_bytes)

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
        scroll_y = min(scroll_y + VIEWPORT_HEIGHT, max_scroll)

    return segments, images, fixed_sticky_records


def stitch(images: list, segments: list, dpr: float, page_height_css: int) -> Image.Image:
    """依 actual scrollY 與 PNG/CSS 比值裁切拼接成整頁長圖，避免重複導覽列。"""
    if not images:
        fail("無任何 segment 可供拼接")

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


def collect_headings(driver) -> list:
    """收集 h1–h6 基本座標與字體資訊（最小版本，完整 Elementor selector 邏輯留給 C2）。"""
    return driver.execute_script(
        """
        var results = [];
        var nodes = document.querySelectorAll('h1, h2, h3, h4, h5, h6');
        for (var i = 0; i < nodes.length; i++) {
            var el = nodes[i];
            var rect = el.getBoundingClientRect();
            var cs = window.getComputedStyle(el);
            results.push({
                tag: el.tagName,
                text: (el.textContent || '').trim().slice(0, 80),
                rect: { top: rect.top, left: rect.left, width: rect.width, height: rect.height },
                font_family: cs.fontFamily,
                font_size: cs.fontSize,
                font_weight: cs.fontWeight,
                line_height: cs.lineHeight
            });
        }
        return results;
        """
    ) or []


def main() -> None:
    metrics: dict = {
        "schema_version": 1,
        "request": {
            "request_id": "c1-spike",
            "requested_url": TARGET_URL,
            "final_url": None,
            "captured_at_utc": datetime.now(timezone.utc).isoformat(),
        },
        "environment": {},
        "viewport": {},
        "document": {},
        "stitch": {"status": "pending", "segments": [], "fixed_sticky_hidden": [], "warnings": []},
        "headings": [],
    }

    options = SafariOptions()
    driver = webdriver.Safari(options=options)

    try:
        caps = driver.capabilities
        browser_name = caps.get("browserName")
        logger.info("session capabilities.browserName=%s", browser_name)
        metrics["environment"]["capabilities"] = {
            k: v for k, v in caps.items() if isinstance(v, (str, int, float, bool))
        }
        if (browser_name or "").lower() != "safari":
            fail(f"capabilities.browserName 不是 safari，實際為 {browser_name}", metrics)

        driver.get(TARGET_URL)
        fonts_status = wait_ready(driver)
        metrics["request"]["final_url"] = driver.current_url

        platform = driver.execute_script("return navigator.platform")
        user_agent = driver.execute_script("return navigator.userAgent")
        max_touch_points = driver.execute_script("return navigator.maxTouchPoints")
        metrics["environment"].update(
            {
                "platform": platform,
                "user_agent": user_agent,
                "max_touch_points": max_touch_points,
            }
        )
        if "Mac" not in (platform or ""):
            fail(f"navigator.platform 不是 macOS 環境，實際為 {platform}", metrics)

        inner = correct_viewport(driver, VIEWPORT_WIDTH, VIEWPORT_HEIGHT)

        is_macos = driver.execute_script(
            "return document.documentElement.classList.contains('is-macos')"
        )
        metrics["document"]["html_class_name"] = driver.execute_script(
            "return document.documentElement.className"
        )
        if not is_macos:
            fail("document.documentElement.classList 不含 is-macos", metrics)

        dpr = driver.execute_script("return window.devicePixelRatio")
        metrics["environment"]["device_pixel_ratio"] = dpr
        if dpr != 2:
            fail(f"window.devicePixelRatio 不是 2，實際為 {dpr}（回報米米裁決，不自行降規）", metrics)

        freeze_dynamics(driver)
        page_height_css = prescroll_until_stable(driver)
        metrics["document"].update(
            {
                "title": driver.execute_script("return document.title"),
                "scroll_width": driver.execute_script("return document.documentElement.scrollWidth"),
                "scroll_height": page_height_css,
                "ready_state": "complete",
                "fonts_status": fonts_status,
            }
        )
        metrics["viewport"] = {
            "inner_width": inner[0],
            "inner_height": inner[1],
        }

        segments, images, fixed_sticky_records = capture_segments(driver, page_height_css)
        metrics["stitch"]["segments"] = segments
        metrics["stitch"]["segment_count"] = len(segments)
        metrics["stitch"]["fixed_sticky_hidden"] = fixed_sticky_records

        # 診斷輸出：第一段 viewport。
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        images[0].save(OUTPUT_DIR / "safari-first-viewport.png")

        full_page = stitch(images, segments, dpr, page_height_css)
        full_page.save(OUTPUT_DIR / "safari-full-page.png")

        expected_width = round(inner[0] * dpr)
        if abs(full_page.width - expected_width) > 1:
            fail(
                f"最終 PNG 寬度 {full_page.width} 與 innerWidth×DPR={expected_width} 誤差超過 1px",
                metrics,
            )

        expected_height = round(page_height_css * dpr)
        if abs(full_page.height - expected_height) > 1:
            fail(
                f"最終 PNG 高度 {full_page.height} 與 document height×DPR={expected_height} 誤差超過 1px",
                metrics,
            )

        metrics["stitch"]["final_png_width"] = full_page.width
        metrics["stitch"]["final_png_height"] = full_page.height
        metrics["stitch"]["status"] = "pass"
        metrics["headings"] = collect_headings(driver)

        write_metrics(metrics)
        logger.info("spike 成功：full-page=%dx%d segments=%d", full_page.width, full_page.height, len(segments))

    finally:
        driver.quit()


if __name__ == "__main__":
    main()
