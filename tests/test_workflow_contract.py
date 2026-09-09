"""正式 Native Safari workflow 的靜態安全與產物契約。"""

from __future__ import annotations

import re
from pathlib import Path


WORKFLOW_PATH = Path(__file__).parents[1] / ".github" / "workflows" / "native-safari.yml"


def _workflow_text() -> str:
    return WORKFLOW_PATH.read_text(encoding="utf-8")


def test_workflow_dispatch_inputs_are_fixed_and_required() -> None:
    text = _workflow_text()
    dispatch = text.split("  workflow_dispatch:", 1)[1].split("\n\npermissions:", 1)[0]

    for input_name in ("url", "request_id", "viewport_width", "viewport_height", "settle_ms"):
        assert re.search(rf"^      {input_name}:$", dispatch, re.MULTILINE)
        assert re.search(
            rf"^      {input_name}:\n(?:(?:        .*|\s*)\n)*?        required: true$",
            dispatch,
            re.MULTILINE,
        )
        assert re.search(
            rf"^      {input_name}:\n(?:(?:        .*|\s*)\n)*?        type: string$",
            dispatch,
            re.MULTILINE,
        )

    for input_name, default in (
        ("viewport_width", "1136"),
        ("viewport_height", "1129"),
        ("settle_ms", "2000"),
    ):
        assert re.search(
            rf"^      {input_name}:\n(?:(?:        .*|\s*)\n)*?        default: '{default}'$",
            dispatch,
            re.MULTILINE,
        )


def test_workflow_runner_permissions_and_timeout_are_locked() -> None:
    text = _workflow_text()

    assert "run-name: Safari capture · ${{ inputs.request_id }}" in text
    assert "permissions:\n  contents: read\n" in text
    assert text.count("permissions:") == 1
    assert "runs-on: macos-15" in text
    assert "timeout-minutes: 10" in text
    assert "macos-latest" not in text


def test_url_reaches_python_only_through_environment() -> None:
    text = _workflow_text()

    assert "NATIVE_CAPTURE_URL: ${{ inputs.url }}" in text
    assert "NATIVE_CAPTURE_REQUEST_ID: ${{ inputs.request_id }}" in text
    assert "NATIVE_CAPTURE_VIEWPORT_WIDTH: ${{ inputs.viewport_width }}" in text
    assert "NATIVE_CAPTURE_VIEWPORT_HEIGHT: ${{ inputs.viewport_height }}" in text
    assert "NATIVE_CAPTURE_SETTLE_MS: ${{ inputs.settle_ms }}" in text
    assert "run: python -m native_capture.cli" in text

    for line in text.splitlines():
        if "${{ inputs.url }}" in line:
            assert line.strip() == "NATIVE_CAPTURE_URL: ${{ inputs.url }}"

    assert "${{ inputs.url }}" not in "\n".join(
        line for line in text.splitlines() if line.lstrip().startswith("run:")
    )


def test_artifact_contract_covers_success_and_failure_outputs() -> None:
    text = _workflow_text()

    assert text.count("if: always()") >= 2
    assert "uses: actions/upload-artifact@v7" in text
    assert "name: safari-capture-${{ inputs.request_id }}" in text
    assert "path: capture-output/" in text
    assert "retention-days: 7" in text
    for required_path in (
        "capture-output/safari-full-page.png",
        "capture-output/safari-first-viewport.png",
        "capture-output/metrics.json",
        "capture-output/versions.txt",
        "capture-output/preview/manifest.json",
        "capture-output/preview/tiles/",
    ):
        assert required_path in text

    assert "if-no-files-found: error" in text
    assert "safaridriver --version" in text
    assert "sw_vers" in text
    assert "uname -m" in text
    assert "python --version" in text


def test_formal_workflow_does_not_reuse_spike_or_inject_site_class() -> None:
    text = _workflow_text()

    for forbidden in (
        "spikes/native_safari_spike.py",
        "native-safari-spike.yml",
        "screenshot.mjs",
        "npm run shot",
        "mac-screenshot.yml",
        "classList.add('is-macos')",
        'classList.add("is-macos")',
    ):
        assert forbidden not in text
