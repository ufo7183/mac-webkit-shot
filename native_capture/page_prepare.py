"""頁面預備階段（對應 SDD §7.1）：字體等待、viewport 校正、預捲穩定、動畫凍結、
fixed／sticky 元素暫時隱藏。邏輯與 C1 spike 完全一致，只是抽成可重用函式。
"""

from __future__ import annotations

import logging
import time

logger = logging.getLogger("native_capture.page_prepare")

MAX_PAGE_HEIGHT_CSS_PX = 60000


class PagePrepareError(RuntimeError):
    """頁面預備階段任一硬條件失敗時拋出。"""

    def __init__(self, message: str, *, fonts_status: str | None = None) -> None:
        super().__init__(message)
        self.fonts_status = fonts_status


_WAIT_FONTS_SCRIPT = """
var callback = arguments[arguments.length - 1];
var timeoutMs = arguments[0];
var settled = false;
var timerId = null;

function finish(status) {
    if (settled) { return; }
    settled = true;
    if (timerId !== null) { clearTimeout(timerId); }
    callback(status);
}

if (!document.fonts || !document.fonts.ready || typeof document.fonts.ready.then !== 'function') {
    finish('unsupported');
} else {
    timerId = setTimeout(function () { finish('timeout'); }, timeoutMs);
    Promise.resolve(document.fonts.ready).then(
        function () { finish('loaded'); },
        function () { finish('error'); }
    );
}
"""


def wait_ready(driver, ready_timeout_s: float = 30, fonts_timeout_ms: int = 8000) -> str:
    """等待 document.readyState 與 document.fonts.ready，回傳字型狀態。

    對應 SDD §7.1 第 2、3 點：readyState 必須到 complete；fonts 狀態記錄
    成功／失敗／timeout，不得靜默略過。
    """
    deadline = time.time() + ready_timeout_s
    state = None
    while time.time() < deadline:
        state = driver.execute_script("return document.readyState")
        if state == "complete":
            break
        time.sleep(0.2)
    if state != "complete":
        raise PagePrepareError(
            f"document.readyState {ready_timeout_s} 秒內未到 complete（最後狀態：{state}）"
        )

    if fonts_timeout_ms <= 0:
        raise PagePrepareError("fonts.ready timeout 必須是正數", fonts_status="error")
    try:
        # Selenium 的 async script timeout 是 WebDriver 邊界的有限總上限，不能只依賴頁面內 timer。
        driver.set_script_timeout(max(1.0, fonts_timeout_ms / 1000.0 + 1.0))
        fonts_status = driver.execute_async_script(_WAIT_FONTS_SCRIPT, fonts_timeout_ms)
    except Exception as exc:  # noqa: BLE001 -- WebDriver 錯誤必須轉成可診斷的硬閘失敗。
        raise PagePrepareError(f"fonts.ready 執行失敗：{exc}", fonts_status="error") from exc
    logger.info("document.fonts.ready 狀態：%s", fonts_status)
    if fonts_status != "loaded":
        raise PagePrepareError(
            f"document.fonts.ready 未完成，狀態：{fonts_status}",
            fonts_status=fonts_status,
        )
    return fonts_status


def collect_viewport_metrics(driver, inner: list[int]) -> dict:
    """收集成功 metrics 所需的 outer、visual viewport 與 screen 真值。"""
    viewport = driver.execute_script(
        """
        return {
            inner_width: window.innerWidth,
            inner_height: window.innerHeight,
            outer_width: window.outerWidth,
            outer_height: window.outerHeight,
            visual_viewport: window.visualViewport ? {
                width: window.visualViewport.width,
                height: window.visualViewport.height,
                scale: window.visualViewport.scale,
                offset_left: window.visualViewport.offsetLeft,
                offset_top: window.visualViewport.offsetTop
            } : {supported: false},
            screen: {
                width: window.screen.width,
                height: window.screen.height,
                avail_width: window.screen.availWidth,
                avail_height: window.screen.availHeight
            }
        };
        """
    )
    if not isinstance(viewport, dict):
        raise PagePrepareError("無法收集 viewport metrics")
    viewport.setdefault("inner_width", inner[0])
    viewport.setdefault("inner_height", inner[1])
    return viewport


def correct_viewport(driver, target_w: int, target_h: int, max_attempts: int = 6) -> list:
    """校正 outer window，直到 inner viewport 與要求值誤差 <=1 CSS px，回傳最終 inner viewport。

    對應 SDD §7.1 第 4 點：不假設 set_window_size() 就等於 inner viewport。
    """
    outer_w, outer_h = target_w, target_h
    inner = [0, 0]
    for attempt in range(max_attempts):
        driver.set_window_rect(x=0, y=0, width=outer_w, height=outer_h)
        inner = driver.execute_script("return [window.innerWidth, window.innerHeight]")
        diff_w = target_w - inner[0]
        diff_h = target_h - inner[1]
        logger.info("校正第 %d 輪：inner=%s 誤差=(%d,%d)", attempt + 1, inner, diff_w, diff_h)
        if abs(diff_w) <= 1 and abs(diff_h) <= 1:
            return inner
        outer_w += diff_w
        outer_h += diff_h
    raise PagePrepareError(
        f"outer window 校正 {max_attempts} 輪後 inner viewport 仍未落在 ±1px"
        f"（目標 {target_w}x{target_h}，實際 {inner}）"
    )


def prescroll_until_stable(
    driver, viewport_height: int, max_rounds: int = 3, settle_s: float = 0.4
) -> int:
    """從頁首逐段預捲到底觸發 lazy-load，最多 max_rounds 輪，連續兩輪 scrollHeight 相同才算穩定。

    對應 SDD §7.1 第 5、6 點與硬上限 60,000 CSS px。
    """
    heights: list[int] = []
    for round_idx in range(max_rounds):
        driver.execute_script("window.scrollTo(0, 0)")
        time.sleep(0.3)
        height = driver.execute_script("return document.body.scrollHeight")
        pos = 0
        while pos < height:
            pos += viewport_height
            driver.execute_script(f"window.scrollTo(0, {pos})")
            time.sleep(settle_s)
            height = driver.execute_script("return document.body.scrollHeight")
        driver.execute_script("window.scrollTo(0, 0)")
        time.sleep(0.3)
        stable_height = driver.execute_script("return document.documentElement.scrollHeight")
        heights.append(stable_height)
        logger.info("預捲第 %d 輪 scrollHeight=%d", round_idx + 1, stable_height)
        if len(heights) >= 2 and heights[-1] == heights[-2]:
            if stable_height > MAX_PAGE_HEIGHT_CSS_PX:
                raise PagePrepareError(
                    f"頁面高度 {stable_height}px 超過上限 {MAX_PAGE_HEIGHT_CSS_PX}px"
                )
            return stable_height
    raise PagePrepareError(f"預捲 {max_rounds} 輪後 scrollHeight 仍不穩定：{heights}")


def freeze_dynamics(driver) -> None:
    """凍結 CSS animation／transition／caret／smooth scroll；只改變動態時間，不改文字或排版。

    對應 SDD §7.1 第 7 點。
    """
    driver.execute_script(
        """
        var style = document.createElement('style');
        style.id = '__native_capture_freeze__';
        style.textContent =
            '*, *::before, *::after { animation-play-state: paused !important; ' +
            'transition: none !important; caret-color: transparent !important; } ' +
            'html { scroll-behavior: auto !important; }';
        document.head.appendChild(style);
        """
    )


def hide_pinned_fixed_sticky(driver) -> list:
    """暫時以 visibility:hidden 隱藏當下 pinned 的 fixed/sticky 元素，保留 layout footprint。

    對應 SDD §7.2：後續段以 visibility:hidden 暫時隱藏，並記錄數量與識別資訊。
    """
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
                    el.setAttribute('data-native-capture-hidden', '1');
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
        var hidden = document.querySelectorAll('[data-native-capture-hidden="1"]');
        for (var i = 0; i < hidden.length; i++) {
            hidden[i].style.removeProperty('visibility');
            hidden[i].removeAttribute('data-native-capture-hidden');
        }
        """
    )
