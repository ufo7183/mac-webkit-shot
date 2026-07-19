"""面板預覽 bundle 產生器（對應 SDD §7.4 manifest schema v1）。

在 runner 拼接裁切區段時同步產生 `preview/manifest.json` 與 `preview/tiles/*.png`。
原始 `safari-full-page.png` 全程不重新解碼成 RGBA、不壓縮、不改名；尺寸只用固定長度
PNG signature／IHDR parser 讀 header，hash 一律以串流方式計算。
"""

from __future__ import annotations

import hashlib
import json
import logging
import struct
from pathlib import Path

from PIL import Image

logger = logging.getLogger("native_capture.preview_bundle")

SCHEMA_VERSION = 1
PREVIEW_MAX_WIDTH = 960
TILE_HEIGHT_LIMIT = 1024
_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


class PreviewBundleError(RuntimeError):
    """preview bundle 產生或驗證失敗時拋出（對應 SDD §7.4：無法產生完整 bundle 必須 fail）。"""


def read_png_dimensions(png_path: Path) -> tuple[int, int]:
    """只讀 PNG signature／IHDR header 取得寬高，不用 Pillow 解碼整張圖。"""
    with png_path.open("rb") as f:
        header = f.read(33)
    if len(header) < 33 or header[:8] != _PNG_SIGNATURE:
        raise PreviewBundleError(f"{png_path} 不是合法 PNG（signature／IHDR 長度不足）")
    if header[12:16] != b"IHDR":
        raise PreviewBundleError(f"{png_path} 缺少 IHDR chunk")
    width, height = struct.unpack(">II", header[16:24])
    return width, height


def sha256_file(path: Path) -> str:
    """以串流方式計算檔案 SHA-256，避免整檔載入記憶體。"""
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _compute_preview_size(source_width: int, source_height: int) -> tuple[int, int, int, int]:
    """依 SDD §7.4 公式計算 preview 寬高：half-up 整數捨入，不用浮點結果跨平台猜整數。

    回傳 (preview_width, preview_height, scale_numerator, scale_denominator)。
    """
    preview_width = min(source_width, PREVIEW_MAX_WIDTH)
    numerator = preview_width
    denominator = source_width
    preview_height = max(
        1, (source_height * preview_width + source_width // 2) // source_width
    )
    return preview_width, preview_height, numerator, denominator


def generate_preview_bundle(
    full_page_path: Path,
    preview_dir: Path,
    artifact_root: Path | None = None,
) -> dict:
    """由已拼接完成的 `safari-full-page.png` 產生 preview bundle，回傳 manifest dict。

    `full_page_path` 必須恰位於 artifact 根目錄（`artifact_root`，預設為其父目錄）；
    `preview_dir` 是 `preview/` 目錄，tiles 一律寫入 `preview_dir/tiles/`。
    """
    artifact_root = artifact_root or full_page_path.parent
    if full_page_path.parent.resolve() != artifact_root.resolve():
        raise PreviewBundleError("safari-full-page.png 必須恰位於 artifact 根目錄")

    source_width, source_height = read_png_dimensions(full_page_path)
    source_sha256 = sha256_file(full_page_path)

    preview_width, preview_height, numerator, denominator = _compute_preview_size(
        source_width, source_height
    )

    tiles_dir = preview_dir / "tiles"
    tiles_dir.mkdir(parents=True, exist_ok=True)

    # 讀原圖產生等比縮小的 preview 圖（僅此步驟需要 Pillow 解碼，用於「產生」預覽，
    # 不是為了預覽而重新解碼「原始」長圖檔案本身——原檔仍以 header-only 方式驗證）。
    with Image.open(full_page_path) as source_img:
        source_img.load()
        preview_img = source_img.resize((preview_width, preview_height), Image.LANCZOS)

    tiles: list[dict] = []
    y = 0
    index = 0
    while y < preview_height:
        tile_height = min(TILE_HEIGHT_LIMIT, preview_height - y)
        tile_img = preview_img.crop((0, y, preview_width, y + tile_height))
        tile_filename = f"tiles/{index:04d}.png"
        tile_path = preview_dir / tile_filename
        tile_img.save(tile_path)
        tile_sha256 = sha256_file(tile_path)
        tiles.append(
            {
                "index": index,
                "file": tile_filename,
                "y": y,
                "width": preview_width,
                "height": tile_height,
                "sha256": tile_sha256,
            }
        )
        y += tile_height
        index += 1

    manifest = {
        "schema_version": SCHEMA_VERSION,
        "source": {
            "file": full_page_path.name,
            "sha256": source_sha256,
            "width": source_width,
            "height": source_height,
        },
        "preview": {
            "format": "png",
            "width": preview_width,
            "height": preview_height,
            "scale": {"numerator": numerator, "denominator": denominator},
            "tile_height_limit": TILE_HEIGHT_LIMIT,
            "tiles": tiles,
        },
    }

    manifest_path = preview_dir / "manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    logger.info(
        "preview bundle 產生完成：%dx%d -> %dx%d，%d 片",
        source_width, source_height, preview_width, preview_height, len(tiles),
    )
    return manifest


def validate_preview_bundle(artifact_root: Path, preview_dir: Path) -> dict:
    """驗證 preview bundle 完整性，通過回傳 manifest dict，失敗拋出 PreviewBundleError。

    對應 SDD §7.4：source 只從 artifact root 解析、tile 只從 preview root 解析，
    兩類路徑都拒絕絕對路徑、反斜線、`..`、符號連結逃逸與解析後越界；缺片、額外未宣告片、
    重複 index／y、洞、重疊、錯誤 rounding、越界路徑、尺寸或 hash 不符皆視為失敗。
    """
    manifest_path = preview_dir / "manifest.json"
    if not manifest_path.is_file():
        raise PreviewBundleError("缺少 preview/manifest.json")

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("schema_version") != SCHEMA_VERSION:
        raise PreviewBundleError(f"schema_version 不是 {SCHEMA_VERSION}")

    source = manifest.get("source", {})
    source_file = source.get("file")
    _assert_safe_relative_path(source_file, "source.file")
    source_path = _resolve_within_root(artifact_root, source_file, "source.file")

    actual_source_w, actual_source_h = read_png_dimensions(source_path)
    if (actual_source_w, actual_source_h) != (source.get("width"), source.get("height")):
        raise PreviewBundleError("source 實際 PNG 尺寸與 manifest 不符")

    actual_source_sha256 = sha256_file(source_path)
    if actual_source_sha256 != source.get("sha256"):
        raise PreviewBundleError("source SHA-256 與 manifest 不符")

    preview = manifest.get("preview", {})
    preview_width = preview.get("width")
    preview_height = preview.get("height")
    expected_w, expected_h, expected_num, expected_den = _compute_preview_size(
        actual_source_w, actual_source_h
    )
    if preview_width != expected_w or preview_height != expected_h:
        raise PreviewBundleError(
            f"preview 尺寸與 half-up 公式不符：期望 {expected_w}x{expected_h}，"
            f"實際 {preview_width}x{preview_height}"
        )
    scale = preview.get("scale", {})
    if scale.get("numerator") != expected_num or scale.get("denominator") != expected_den:
        raise PreviewBundleError("scale numerator/denominator 與 preview.width/source.width 不符")

    if preview.get("tile_height_limit") != TILE_HEIGHT_LIMIT:
        raise PreviewBundleError(f"tile_height_limit 不是固定值 {TILE_HEIGHT_LIMIT}")

    tiles = preview.get("tiles", [])
    if not tiles:
        raise PreviewBundleError("tiles 不得為空")

    _validate_tiles(tiles, preview_width, preview_height, preview_dir)

    return manifest


def _validate_tiles(tiles: list, preview_width: int, preview_height: int, preview_dir: Path) -> None:
    """驗證 tiles 的 index／y／width／height／連續座標／末片規則與實際檔案。"""
    sorted_tiles = sorted(tiles, key=lambda t: t.get("index", -1))
    seen_indices = set()
    expected_y = 0

    for i, tile in enumerate(sorted_tiles):
        index = tile.get("index")
        if index != i:
            raise PreviewBundleError(f"tile index 不連續或不從 0 開始：期望 {i}，實際 {index}")
        if index in seen_indices:
            raise PreviewBundleError(f"tile index 重複：{index}")
        seen_indices.add(index)

        expected_file = f"tiles/{index:04d}.png"
        tile_file = tile.get("file")
        if tile_file != expected_file:
            raise PreviewBundleError(f"tile file 命名不符：期望 {expected_file}，實際 {tile_file}")
        _assert_safe_relative_path(tile_file, "preview.tiles[*].file")
        tile_path = _resolve_within_root(preview_dir, tile_file, "preview.tiles[*].file")

        tile_y = tile.get("y")
        if tile_y != expected_y:
            raise PreviewBundleError(
                f"tile[{index}].y 與連續座標不符：期望 {expected_y}，實際 {tile_y}"
            )

        tile_width = tile.get("width")
        if tile_width != preview_width:
            raise PreviewBundleError(f"tile[{index}].width 必須等於 preview.width={preview_width}")

        tile_height = tile.get("height")
        is_last = i == len(sorted_tiles) - 1
        if is_last:
            if not (1 <= tile_height <= TILE_HEIGHT_LIMIT):
                raise PreviewBundleError(f"末片 tile[{index}].height 必須落在 1..{TILE_HEIGHT_LIMIT}")
            if tile_y + tile_height != preview_height:
                raise PreviewBundleError(
                    f"末片 tile[{index}] y+height 必須等於 preview.height={preview_height}"
                )
        elif tile_height != TILE_HEIGHT_LIMIT:
            raise PreviewBundleError(f"非末片 tile[{index}].height 必須固定為 {TILE_HEIGHT_LIMIT}")

        actual_w, actual_h = read_png_dimensions(tile_path)
        if (actual_w, actual_h) != (tile_width, tile_height):
            raise PreviewBundleError(f"tile[{index}] 實際 PNG 尺寸與 manifest 不符")

        actual_sha256 = sha256_file(tile_path)
        if actual_sha256 != tile.get("sha256"):
            raise PreviewBundleError(f"tile[{index}] SHA-256 與 manifest 不符")

        expected_y = tile_y + tile_height

    if expected_y != preview_height:
        raise PreviewBundleError(
            f"tiles 總高度 {expected_y} 與 preview.height={preview_height} 不符（缺片或有洞）"
        )


def _assert_safe_relative_path(value: object, field_name: str) -> None:
    """拒絕絕對路徑、反斜線、`..` 與非字串值。"""
    if not isinstance(value, str) or not value:
        raise PreviewBundleError(f"{field_name} 必須是非空字串")
    if "\\" in value:
        raise PreviewBundleError(f"{field_name} 不得含反斜線：{value}")
    if value.startswith("/") or (len(value) > 1 and value[1] == ":"):
        raise PreviewBundleError(f"{field_name} 不得是絕對路徑：{value}")
    if ".." in Path(value).parts:
        raise PreviewBundleError(f"{field_name} 不得含 ..：{value}")


def _resolve_within_root(root: Path, relative: str, field_name: str) -> Path:
    """解析相對路徑並確認結果仍在 root 之內（拒絕符號連結逃逸與解析後越界）。"""
    root_resolved = root.resolve()
    candidate = (root / relative).resolve()
    try:
        candidate.relative_to(root_resolved)
    except ValueError as exc:
        raise PreviewBundleError(f"{field_name} 解析後越界 root：{relative}") from exc
    if not candidate.is_file():
        raise PreviewBundleError(f"{field_name} 指向的檔案不存在：{relative}")
    return candidate
