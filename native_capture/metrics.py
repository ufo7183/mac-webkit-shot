"""metrics.json 產生器（對應 SDD §8 schema_version 1）。

skeleton 與寫檔邏輯沿用 C1 spike；heading 收集在 spike 最小版本（僅 h1–h6 基本欄位）
基礎上擴充：加入 `.elementor-heading-title`、可重現 selector／Elementor data-id、
position／top 與父層 display／align-items，不寫死任何特定頁面的 selector。
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger("native_capture.metrics")

SCHEMA_VERSION = 1

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


def collect_headings(driver) -> list:
    """收集 h1–h6 與 `.elementor-heading-title` 的 computed style 與座標。

    對應 SDD §8：每筆至少含文字、可重現 selector／Elementor data-id、
    getBoundingClientRect()、font family/size/weight/line-height、
    position/top、父層 display/align-items；不寫死任何特定頁面的 selector。
    """
    return driver.execute_script(_COLLECT_HEADINGS_JS) or []
