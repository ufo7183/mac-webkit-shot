"""metrics.json 產生器（對應 SDD §8 schema_version 1）。

skeleton 與寫檔邏輯沿用 C1 spike；heading 收集在 spike 最小版本（僅 h1–h6 基本欄位）
基礎上擴充：加入 `.elementor-heading-title`、可重現 selector／Elementor data-id、
position／top 與父層 display／align-items，不寫死任何特定頁面的 selector。
"""

from __future__ import annotations

import json
import logging
import math
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger("native_capture.metrics")

SCHEMA_VERSION = 1


class MetricsError(ValueError):
    """成功 metrics 缺少可驗證真值時拋出。"""

_COLLECT_HEADINGS_JS = """
function buildSelector(el) {
    if (!el || el.nodeType !== 1) { return null; }
    var parts = [];
    var node = el;
    while (node && node.nodeType === 1 && node !== document.body) {
        var tag = node.tagName.toLowerCase();
        var parent = node.parentElement;
        if (!parent) { parts.unshift(tag); break; }
        var siblings = Array.prototype.filter.call(parent.children, function (sib) {
            return sib.tagName === node.tagName;
        });
        if (siblings.length > 1) {
            var idx = Array.prototype.indexOf.call(siblings, node) + 1;
            tag += ':nth-of-type(' + idx + ')';
        }
        parts.unshift(tag);
        node = parent;
    }
    return 'body > ' + parts.join(' > ');
}

function elementorDataId(el) {
    var host = el.closest('[data-id]');
    return host ? host.getAttribute('data-id') : null;
}

var results = [];
var seen = new Set();
var nodes = document.querySelectorAll('h1, h2, h3, h4, h5, h6, .elementor-heading-title');
for (var i = 0; i < nodes.length; i++) {
    var el = nodes[i];
    if (seen.has(el)) { continue; }
    seen.add(el);
    var rect = el.getBoundingClientRect();
    var cs = window.getComputedStyle(el);
    var parent = el.parentElement;
    var parentCs = parent ? window.getComputedStyle(parent) : null;
    results.push({
        tag: el.tagName,
        text: (el.textContent || '').trim().slice(0, 80),
        selector: buildSelector(el),
        elementor_data_id: elementorDataId(el),
        rect: { top: rect.top, left: rect.left, width: rect.width, height: rect.height },
        font_family: cs.fontFamily,
        font_size: cs.fontSize,
        font_weight: cs.fontWeight,
        line_height: cs.lineHeight,
        position: cs.position,
        css_top: cs.top,
        parent_display: parentCs ? parentCs.display : null,
        parent_align_items: parentCs ? parentCs.alignItems : null
    });
}
return results;
"""


def new_metrics_skeleton(request_id: str, requested_url: str) -> dict:
    """建立 metrics.json 骨架（schema_version 1），對應 SDD §8。"""
    return {
        "schema_version": SCHEMA_VERSION,
        "request": {
            "request_id": request_id,
            "requested_url": requested_url,
            "final_url": None,
            "captured_at_utc": datetime.now(timezone.utc).isoformat(),
        },
        "environment": {},
        "viewport": {},
        "document": {},
        "stitch": {
            "status": "pending",
            "segment_count": 0,
            "segments": [],
            "fixed_sticky_hidden": [],
            "final_png_width": 0,
            "final_png_height": 0,
            "warnings": [],
        },
        "headings": [],
    }


def write_metrics(metrics: dict, output_dir: Path) -> Path:
    """寫出 metrics.json，回傳寫入路徑。"""
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / "metrics.json"
    path.write_text(json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def validate_success_metrics(metrics: dict) -> None:
    """驗證成功輸出已填入 SDD §8 的環境、viewport、字型與 segments 真值。"""
    if metrics.get("schema_version") != SCHEMA_VERSION:
        raise MetricsError(f"schema_version 不是 {SCHEMA_VERSION}")

    request = metrics.get("request", {})
    if not request.get("final_url"):
        raise MetricsError("success metrics 缺少 request.final_url")

    environment = metrics.get("environment", {})
    for field in ("macos_version", "safari_version", "safaridriver_version", "user_agent", "platform"):
        if environment.get(field) in (None, ""):
            raise MetricsError(f"success metrics 缺少 environment.{field}")
    if not isinstance(environment.get("capabilities"), dict) or not environment["capabilities"]:
        raise MetricsError("success metrics 缺少 environment.capabilities")
    dpr = environment.get("device_pixel_ratio")
    if isinstance(dpr, bool) or not isinstance(dpr, (int, float)) or not math.isfinite(dpr) or dpr < 1:
        raise MetricsError(f"success metrics 的 device_pixel_ratio 無效：{dpr}")

    viewport = metrics.get("viewport", {})
    for field in ("inner_width", "inner_height", "outer_width", "outer_height"):
        value = viewport.get(field)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
            raise MetricsError(f"success metrics 的 viewport.{field} 無效：{value}")
    if not isinstance(viewport.get("visual_viewport"), dict) or not viewport["visual_viewport"]:
        raise MetricsError("success metrics 缺少 viewport.visual_viewport")
    if not isinstance(viewport.get("screen"), dict) or not viewport["screen"]:
        raise MetricsError("success metrics 缺少 viewport.screen")

    document = metrics.get("document", {})
    if not isinstance(document.get("title"), str) or not isinstance(document.get("html_class_name"), str):
        raise MetricsError("success metrics 缺少 document title 或 html class 真值")
    if document.get("ready_state") != "complete" or document.get("fonts_status") != "loaded":
        raise MetricsError("success metrics 的 document.ready_state 或 fonts_status 未完成")
    for field in ("scroll_width", "scroll_height"):
        value = document.get(field)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
            raise MetricsError(f"success metrics 的 document.{field} 無效：{value}")

    stitch = metrics.get("stitch", {})
    segments = stitch.get("segments")
    if stitch.get("status") != "pass" or not isinstance(segments, list) or not segments:
        raise MetricsError("success metrics 缺少通過的 segments")
    if stitch.get("segment_count") != len(segments):
        raise MetricsError("success metrics 的 segment_count 與 segments 不一致")
    for index, segment in enumerate(segments):
        for field in (
            "actual_scroll_y",
            "inner_width",
            "inner_height",
            "png_width",
            "png_height",
        ):
            if field not in segment or segment[field] is None:
                raise MetricsError(f"success metrics 的 segment[{index}] 缺少 {field}")
    if stitch.get("final_png_width", 0) <= 0 or stitch.get("final_png_height", 0) <= 0:
        raise MetricsError("success metrics 缺少 final PNG 尺寸")
    if not isinstance(metrics.get("headings"), list):
        raise MetricsError("success metrics 缺少 headings")


def collect_headings(driver) -> list:
    """收集 h1–h6 與 `.elementor-heading-title` 的 computed style 與座標。

    對應 SDD §8：每筆至少含文字、可重現 selector／Elementor data-id、
    getBoundingClientRect()、font family/size/weight/line-height、
    position/top、父層 display/align-items；不寫死任何特定頁面的 selector。
    """
    return driver.execute_script(_COLLECT_HEADINGS_JS) or []
