"""native_capture.preview_bundle 的測試：正常 round-trip、half-up rounding 公式、
以及 manifest 攻擊案例（根逃逸、錯 rounding、錯 index／檔名、洞、重疊 y、末片錯高、
實際 PNG 尺寸或 hash 不符）全部必須 fail-closed。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from PIL import Image

from native_capture.preview_bundle import (
    PreviewBundleError,
    TILE_HEIGHT_LIMIT,
    _compute_preview_size,
    generate_preview_bundle,
    validate_preview_bundle,
)


def _make_source_png(path: Path, width: int, height: int, color: tuple[int, int, int] = (10, 20, 30)) -> None:
    Image.new("RGB", (width, height), color).save(path)


def _load_manifest(preview_dir: Path) -> dict:
    return json.loads((preview_dir / "manifest.json").read_text(encoding="utf-8"))


def _save_manifest(preview_dir: Path, manifest: dict) -> None:
    (preview_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )


@pytest.fixture()
def bundle(tmp_path: Path) -> tuple[Path, Path]:
    """產生一個 200x1500 原圖的正常 preview bundle（2 片：1024 + 476）。"""
    artifact_root = tmp_path / "capture-output"
    artifact_root.mkdir()
    full_page_path = artifact_root / "safari-full-page.png"
    _make_source_png(full_page_path, 200, 1500)
    preview_dir = artifact_root / "preview"
    generate_preview_bundle(full_page_path, preview_dir, artifact_root)
    return artifact_root, preview_dir


class TestGenerateAndValidateRoundtrip:
    def test_roundtrip_passes(self, bundle: tuple[Path, Path]) -> None:
        artifact_root, preview_dir = bundle
        manifest = validate_preview_bundle(artifact_root, preview_dir)
        assert manifest["schema_version"] == 1
        assert manifest["preview"]["width"] == 200
        assert manifest["preview"]["height"] == 1500
        assert len(manifest["preview"]["tiles"]) == 2
        assert manifest["preview"]["tiles"][0]["height"] == TILE_HEIGHT_LIMIT
        assert manifest["preview"]["tiles"][1]["height"] == 1500 - TILE_HEIGHT_LIMIT


class TestComputePreviewSizeHalfUp:
    def test_half_up_rounding_matches_sdd_formula(self) -> None:
        width, height, numerator, denominator = _compute_preview_size(2272, 4001)
        assert (width, height, numerator, denominator) == (960, 1691, 960, 2272)


class TestManifestAttacks:
    def test_source_root_escape_rejected(self, bundle: tuple[Path, Path]) -> None:
        artifact_root, preview_dir = bundle
        manifest = _load_manifest(preview_dir)
        manifest["source"]["file"] = "../escape.png"
        _save_manifest(preview_dir, manifest)
        with pytest.raises(PreviewBundleError):
            validate_preview_bundle(artifact_root, preview_dir)

    def test_source_absolute_path_rejected(self, bundle: tuple[Path, Path]) -> None:
        artifact_root, preview_dir = bundle
        manifest = _load_manifest(preview_dir)
        manifest["source"]["file"] = "/etc/passwd"
        _save_manifest(preview_dir, manifest)
        with pytest.raises(PreviewBundleError):
            validate_preview_bundle(artifact_root, preview_dir)

    def test_tile_root_escape_rejected(self, bundle: tuple[Path, Path]) -> None:
        artifact_root, preview_dir = bundle
        manifest = _load_manifest(preview_dir)
        manifest["preview"]["tiles"][0]["file"] = "../../evil.png"
        _save_manifest(preview_dir, manifest)
        with pytest.raises(PreviewBundleError):
            validate_preview_bundle(artifact_root, preview_dir)

    def test_wrong_rounding_height_rejected(self, bundle: tuple[Path, Path]) -> None:
        artifact_root, preview_dir = bundle
        manifest = _load_manifest(preview_dir)
        manifest["preview"]["height"] = manifest["preview"]["height"] + 1
        _save_manifest(preview_dir, manifest)
        with pytest.raises(PreviewBundleError, match="rounding|不符"):
            validate_preview_bundle(artifact_root, preview_dir)

    def test_skipped_index_rejected(self, bundle: tuple[Path, Path]) -> None:
        artifact_root, preview_dir = bundle
        manifest = _load_manifest(preview_dir)
        manifest["preview"]["tiles"][1]["index"] = 2
        _save_manifest(preview_dir, manifest)
        with pytest.raises(PreviewBundleError, match="index"):
            validate_preview_bundle(artifact_root, preview_dir)

    def test_wrong_filename_typo_rejected(self, bundle: tuple[Path, Path]) -> None:
        artifact_root, preview_dir = bundle
        manifest = _load_manifest(preview_dir)
        manifest["preview"]["tiles"][0]["file"] = "tiles/0000x.png"
        _save_manifest(preview_dir, manifest)
        with pytest.raises(PreviewBundleError, match="命名"):
            validate_preview_bundle(artifact_root, preview_dir)

    def test_gap_between_tiles_rejected(self, bundle: tuple[Path, Path]) -> None:
        artifact_root, preview_dir = bundle
        manifest = _load_manifest(preview_dir)
        manifest["preview"]["tiles"][1]["y"] = manifest["preview"]["tiles"][1]["y"] + 1
        _save_manifest(preview_dir, manifest)
        with pytest.raises(PreviewBundleError):
            validate_preview_bundle(artifact_root, preview_dir)

    def test_overlap_between_tiles_rejected(self, bundle: tuple[Path, Path]) -> None:
        artifact_root, preview_dir = bundle
        manifest = _load_manifest(preview_dir)
        manifest["preview"]["tiles"][1]["y"] = manifest["preview"]["tiles"][1]["y"] - 1
        _save_manifest(preview_dir, manifest)
        with pytest.raises(PreviewBundleError):
            validate_preview_bundle(artifact_root, preview_dir)

    def test_last_tile_wrong_height_rejected(self, bundle: tuple[Path, Path]) -> None:
        artifact_root, preview_dir = bundle
        manifest = _load_manifest(preview_dir)
        manifest["preview"]["tiles"][-1]["height"] = manifest["preview"]["tiles"][-1]["height"] + 5
        _save_manifest(preview_dir, manifest)
        with pytest.raises(PreviewBundleError, match="末片"):
            validate_preview_bundle(artifact_root, preview_dir)

    def test_last_tile_height_out_of_range_rejected(self, bundle: tuple[Path, Path]) -> None:
        artifact_root, preview_dir = bundle
        manifest = _load_manifest(preview_dir)
        manifest["preview"]["tiles"][-1]["height"] = TILE_HEIGHT_LIMIT + 100
        _save_manifest(preview_dir, manifest)
        with pytest.raises(PreviewBundleError, match="末片"):
            validate_preview_bundle(artifact_root, preview_dir)

    def test_actual_tile_png_size_mismatch_rejected(self, bundle: tuple[Path, Path]) -> None:
        artifact_root, preview_dir = bundle
        tile_path = preview_dir / "tiles" / "0000.png"
        _make_source_png(tile_path, 199, TILE_HEIGHT_LIMIT)  # manifest 仍記 200 寬。
        with pytest.raises(PreviewBundleError, match="尺寸"):
            validate_preview_bundle(artifact_root, preview_dir)

    def test_tile_hash_mismatch_rejected(self, bundle: tuple[Path, Path]) -> None:
        artifact_root, preview_dir = bundle
        tile_path = preview_dir / "tiles" / "0000.png"
        _make_source_png(tile_path, 200, TILE_HEIGHT_LIMIT, color=(99, 99, 99))  # 尺寸同、內容不同。
        with pytest.raises(PreviewBundleError, match="SHA-256"):
            validate_preview_bundle(artifact_root, preview_dir)

    def test_source_png_size_mismatch_rejected(self, bundle: tuple[Path, Path]) -> None:
        artifact_root, preview_dir = bundle
        full_page_path = artifact_root / "safari-full-page.png"
        _make_source_png(full_page_path, 201, 1500)  # manifest 仍記 200 寬。
        with pytest.raises(PreviewBundleError, match="尺寸"):
            validate_preview_bundle(artifact_root, preview_dir)

    def test_source_hash_mismatch_rejected(self, bundle: tuple[Path, Path]) -> None:
        artifact_root, preview_dir = bundle
        full_page_path = artifact_root / "safari-full-page.png"
        _make_source_png(full_page_path, 200, 1500, color=(200, 200, 200))  # 尺寸同、內容不同。
        with pytest.raises(PreviewBundleError, match="SHA-256"):
            validate_preview_bundle(artifact_root, preview_dir)

    def test_missing_manifest_rejected(self, tmp_path: Path) -> None:
        artifact_root = tmp_path / "empty"
        artifact_root.mkdir()
        preview_dir = artifact_root / "preview"
        preview_dir.mkdir()
        with pytest.raises(PreviewBundleError, match="manifest"):
            validate_preview_bundle(artifact_root, preview_dir)


class TestGenerateRootConstraint:
    def test_full_page_path_must_be_at_artifact_root(self, tmp_path: Path) -> None:
        artifact_root = tmp_path / "capture-output"
        artifact_root.mkdir()
        nested = artifact_root / "nested"
        nested.mkdir()
        full_page_path = nested / "safari-full-page.png"
        _make_source_png(full_page_path, 200, 300)
        with pytest.raises(PreviewBundleError, match="artifact 根目錄"):
            generate_preview_bundle(full_page_path, artifact_root / "preview", artifact_root)
