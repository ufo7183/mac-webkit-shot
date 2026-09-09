"""page_prepare 的真實 JavaScript 字型等待 fixture 測試。"""

from __future__ import annotations

import json
import shutil
import subprocess

import pytest

from native_capture.page_prepare import PagePrepareError, wait_ready


class _NodeFontsDriver:
    """在 Node VM 執行 production async script，隔離 WebDriver 邊界。"""

    def __init__(self, mode: str) -> None:
        self.mode = mode
        self.script_timeout_s: float | None = None
        self.callback_count = 0

    def execute_script(self, script: str, *args: object) -> object:
        assert "document.readyState" in script
        return "complete"

    def set_script_timeout(self, timeout_s: float) -> None:
        self.script_timeout_s = timeout_s

    def execute_async_script(self, script: str, *args: object) -> str:
        node = shutil.which("node")
        if node is None:
            pytest.skip("此 fixture 需要 Node.js 執行隔離 JavaScript")
        timeout_ms = int(args[0])
        node_program = f"""
const vm = require('node:vm');
const source = {json.dumps(script)};
const mode = {json.dumps(self.mode)};
const timeoutMs = {timeout_ms};
const callbacks = [];
let ready;
if (mode === 'unsupported') {{
  ready = undefined;
}} else if (mode === 'reject') {{
  ready = Promise.reject(new Error('font fixture rejection'));
}} else if (mode === 'timeout') {{
  ready = new Promise(() => {{}});
}} else {{
  ready = new Promise((resolve) => setTimeout(resolve, 5));
}}
const sandbox = {{
  document: mode === 'unsupported' ? {{}} : {{fonts: {{ready}}}},
  Promise,
  setTimeout,
  clearTimeout,
  arguments: [timeoutMs, (value) => callbacks.push(String(value))],
}};
vm.runInNewContext(source, sandbox);
setTimeout(() => {{
  if (callbacks.length !== 1) {{
    process.stderr.write('callback_count=' + callbacks.length);
    process.exit(2);
  }}
  process.stdout.write(callbacks[0] + '|' + callbacks.length);
}}, timeoutMs + 30);
"""
        result = subprocess.run(
            [node, "-e", node_program],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or "Node fixture failed")
        status, count = result.stdout.strip().split("|")
        self.callback_count = int(count)
        return status


def test_wait_ready_executes_async_fonts_script_and_accepts_only_loaded() -> None:
    driver = _NodeFontsDriver("loaded")

    assert wait_ready(driver, ready_timeout_s=1, fonts_timeout_ms=100) == "loaded"
    assert driver.script_timeout_s is not None
    assert 0 < driver.script_timeout_s < 10
    assert driver.callback_count == 1


@pytest.mark.parametrize("mode", ["timeout", "reject", "unsupported"])
def test_wait_ready_rejects_timeout_error_and_unsupported_status(mode: str) -> None:
    driver = _NodeFontsDriver(mode)

    with pytest.raises(PagePrepareError, match="字型|fonts.ready") as exc_info:
        wait_ready(driver, ready_timeout_s=1, fonts_timeout_ms=10)

    assert exc_info.value.fonts_status == ("timeout" if mode == "timeout" else mode)
    assert driver.callback_count == 1
