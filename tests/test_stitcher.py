"""native_capture.stitcher 與 page_prepare 的測試：正常拼接、尾段重疊、DPR2、
頁高變動、超高頁、fixed/sticky、尺寸不一致。stitch()／verify_stitched_dimensions()
是純函式，不需要真實 Selenium；capture_segments()／prescroll_until_stable() 用
FakeDriver 模擬 execute_script 回傳值。
"""

from __future__ import annotations

import io
import re
from pathlib import Path

import pytest
from PIL import Image

from native_capture.page_prepare import PagePrepareError, prescroll_until_stable
from native_capture.stitcher import StitchError, capture_segments, stitch, verify_stitched_dimensions


def _solid_image(width: int, height: int, color: tuple[int, int, int]) -> Image.Image:
    return Image.new("RGB", (width, height), color)


def _seg(index: int, actual_scroll_y: int, inner_w: int, inner_h: int, png_w: int, png_h: int) -> dict:
    return {
        "index": index,
        "requested_scroll_y": actual_scroll_y,
        "actual_scroll_y": actual_scroll_y,
        "inner_width": inner_w,
        "inner_height": inner_h,
        "png_width": png_w,
        "png_height": png_h,
    }


class TestStitchNormal:
    def test_two_segments_no_overlap(self) -> None:
        img0 = _solid_image(800, 600, (255, 0, 0))
        img1 = _solid_image(800, 600, (0, 255, 0))
        segments = [_seg(0, 0, 800, 600, 800, 600), _seg(1, 600, 800, 600, 800, 600)]
        canvas = stitch([img0, img1], segments, dpr=1, page_height_css=1200)
        assert canvas.size == (800, 1200)
        assert canvas.getpixel((10, 10)) == (255, 0, 0)
        assert canvas.getpixel((10, 1190)) == (0, 255, 0)


class TestStitchTailOverlap:
    def test_second_segment_overlaps_first(self) -> None:
        # 頁高只有 1000，第二段 requested/actual scrollY=400（viewport 600），
        # 與第一段底部（600）重疊 200px，拼接必須裁掉重疊區域不留重複內容。
        img0 = _solid_image(800, 600, (255, 0, 0))
        img1 = _solid_image(800, 600, (0, 255, 0))
        segments = [_seg(0, 0, 800, 600, 800, 600), _seg(1, 400, 800, 600, 800, 600)]
        canvas = stitch([img0, img1], segments, dpr=1, page_height_css=1000)
        assert canvas.size == (800, 1000)
        # 重疊區域（y=400~600）拼接後應為第二段內容（後貼上者覆蓋）。
        assert canvas.getpixel((10, 999)) == (0, 255, 0)
        assert canvas.getpixel((10, 10)) == (255, 0, 0)

    def test_tail_crop_is_allowed_when_coverage_reaches_page_end(self) -> None:
        img0 = _solid_image(800, 600, (255, 0, 0))
        img1 = _solid_image(800, 600, (0, 255, 0))
        segments = [_seg(0, 0, 800, 600, 800, 600), _seg(1, 400, 800, 600, 800, 600)]

        canvas = stitch([img0, img1], segments, dpr=1, page_height_css=900)

        assert canvas.size == (800, 900)
        assert canvas.getpixel((10, 899)) == (0, 255, 0)


class TestStitchDpr2:
    def test_dpr2_scales_canvas_and_positions(self) -> None:
        img0 = _solid_image(1600, 1200, (255, 0, 0))
        img1 = _solid_image(1600, 1200, (0, 255, 0))
        segments = [_seg(0, 0, 800, 600, 1600, 1200), _seg(1, 600, 800, 600, 1600, 1200)]
        canvas = stitch([img0, img1], segments, dpr=2, page_height_css=1200)
        assert canvas.size == (1600, 2400)
        verify_stitched_dimensions(canvas, inner_width=800, dpr=2, page_height_css=1200)


class TestStitchInconsistentSegmentSizes:
    def test_last_segment_shorter(self) -> None:
        img0 = _solid_image(800, 600, (255, 0, 0))
        img1 = _solid_image(800, 200, (0, 255, 0))  # 尾段實際擷取較短。
        segments = [_seg(0, 0, 800, 600, 800, 600), _seg(1, 600, 800, 200, 800, 200)]
        canvas = stitch([img0, img1], segments, dpr=1, page_height_css=800)
        assert canvas.size == (800, 800)


class TestStitchFailClosed:
    def test_no_images_raises(self) -> None:
        with pytest.raises(StitchError, match="無任何 segment"):
            stitch([], [], dpr=1, page_height_css=100)

    def test_verify_dimensions_width_mismatch_raises(self) -> None:
        canvas = _solid_image(797, 1000, (0, 0, 0))
        with pytest.raises(StitchError, match="寬度"):
            verify_stitched_dimensions(canvas, inner_width=800, dpr=1, page_height_css=1000)

    def test_verify_dimensions_height_mismatch_raises(self) -> None:
        canvas = _solid_image(800, 990, (0, 0, 0))
        with pytest.raises(StitchError, match="高度"):
            verify_stitched_dimensions(canvas, inner_width=800, dpr=1, page_height_css=1000)

    def test_verify_dimensions_within_1px_rounding_passes(self) -> None:
        # 頁高變動：document height 由 999.6 四捨五入為 1000，允許 1px 誤差。
        canvas = _solid_image(800, 999, (0, 0, 0))
        verify_stitched_dimensions(canvas, inner_width=800, dpr=1, page_height_css=1000)

    @pytest.mark.parametrize(
        ("name", "images", "segments"),
        [
            (
                "missing_tail",
                [_solid_image(8, 4, (255, 0, 0))],
                [_seg(0, 0, 8, 4, 8, 4)],
            ),
            (
                "middle_gap",
                [
                    _solid_image(8, 4, (255, 0, 0)),
                    _solid_image(8, 4, (0, 0, 255)),
                ],
                [_seg(0, 0, 8, 4, 8, 4), _seg(1, 6, 8, 4, 8, 4)],
            ),
        ],
    )
    def test_incomplete_capture_is_rejected_before_canvas_size_can_mask_it(
        self, name: str, images: list[Image.Image], segments: list[dict]
    ) -> None:
        with pytest.raises(StitchError, match="完整|缺口|尾"):
            stitch(images, segments, dpr=1, page_height_css=10)

    def test_mismatched_image_and_segment_counts_are_rejected(self) -> None:
        image = _solid_image(8, 10, (255, 0, 0))
        with pytest.raises(StitchError, match="數量"):
            stitch([image], [], dpr=1, page_height_css=10)

    def test_wrong_pixel_ratio_is_rejected(self) -> None:
        image = _solid_image(9, 10, (255, 0, 0))
        with pytest.raises(StitchError, match="比例|尺寸"):
            stitch([image], [_seg(0, 0, 8, 10, 9, 10)], dpr=1, page_height_css=10)

    def test_segment_stall_is_rejected_in_capture(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("native_capture.stitcher.time.sleep", lambda _s: None)
        driver = _SegmentFakeDriver(_png_bytes(800, 600), stall=True)
        with pytest.raises(StitchError, match="停滯|前進"):
            capture_segments(driver, page_height_css=1400, viewport_height=600, segments_dir=tmp_path, settle_ms=0)

    def test_page_height_change_is_rejected_during_capture(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("native_capture.stitcher.time.sleep", lambda _s: None)
        driver = _SegmentFakeDriver(_png_bytes(800, 600), height_after_first=1500)
        with pytest.raises(StitchError, match="頁高改變"):
            capture_segments(driver, page_height_css=1400, viewport_height=600, segments_dir=tmp_path, settle_ms=0)

    def test_segment_limit_is_rejected_before_claiming_bottom(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("native_capture.stitcher.time.sleep", lambda _s: None)
        monkeypatch.setattr("native_capture.stitcher.MAX_SEGMENTS", 2)
        driver = _SegmentFakeDriver(_png_bytes(800, 600), scroll_height=2000)
        with pytest.raises(StitchError, match="segment 上限"):
            capture_segments(driver, page_height_css=2000, viewport_height=600, segments_dir=tmp_path, settle_ms=0)


class _ScrollFakeDriver:
    """模擬 prescroll_until_stable 所需的 execute_script 回應。"""

    def __init__(self, scroll_height: int) -> None:
        self.scroll_height = scroll_height

    def execute_script(self, script: str, *args) -> object:
        if "document.body.scrollHeight" in script:
            return self.scroll_height
        if "document.documentElement.scrollHeight" in script:
            return self.scroll_height
        if "window.scrollTo" in script:
            return None
        raise AssertionError(f"未預期的 script：{script}")


class TestPrescrollOversizedPage:
    def test_raises_when_exceeds_max_height(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("native_capture.page_prepare.time.sleep", lambda _s: None)
        driver = _ScrollFakeDriver(scroll_height=70000)
        with pytest.raises(PagePrepareError, match="超過上限"):
            prescroll_until_stable(driver, viewport_height=600)


class _SegmentFakeDriver:
    """模擬 capture_segments 所需的 execute_script／get_screenshot_as_png。"""

    def __init__(
        self,
        png_bytes: bytes,
        stall: bool = False,
        height_after_first: int | None = None,
        scroll_height: int = 1400,
    ) -> None:
        self._scroll_y = 0
        self._png_bytes = png_bytes
        self._stall = stall
        self._height_after_first = height_after_first
        self._scroll_height = scroll_height
        self._height_reads = 0
        self.hide_calls = 0
        self.restore_calls = 0

    def execute_script(self, script: str, *args) -> object:
        if "window.scrollTo" in script:
            match = re.search(r"window\.scrollTo\(0,\s*(\d+)\)", script)
            self._scroll_y = int(match.group(1))
            return None
        if "window.scrollY" in script:
            return 0 if self._stall else self._scroll_y
        if "document.documentElement.scrollHeight" in script:
            self._height_reads += 1
            if self._height_after_first is not None and self._height_reads > 1:
                return self._height_after_first
            return self._scroll_height
        if "innerWidth, window.innerHeight" in script:
            return [800, 600]
        if "removeProperty" in script:
            self.restore_calls += 1
            return None
        if "data-native-capture-hidden" in script:
            self.hide_calls += 1
            return []
        raise AssertionError(f"未預期的 script：{script}")

    def get_screenshot_as_png(self) -> bytes:
        return self._png_bytes


def _png_bytes(width: int, height: int) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (width, height), (1, 2, 3)).save(buf, format="PNG")
    return buf.getvalue()


class TestCaptureSegmentsFixedSticky:
    def test_hides_and_restores_after_first_segment_only(self, tmp_path: Path) -> None:
        driver = _SegmentFakeDriver(_png_bytes(800, 600))
        segments_dir = tmp_path / "segments"
        segments, images, fixed_sticky = capture_segments(
            driver, page_height_css=1400, viewport_height=600, segments_dir=segments_dir, settle_ms=0
        )
        assert len(segments) == 3
        assert len(images) == 3
        # 第一段（index 0）不觸發 hide；之後每段各觸發一次 hide/restore。
        assert driver.hide_calls == 2
        assert driver.restore_calls == 2
        # fail-closed 前提：即使後續步驟失敗，segments 仍已落檔可供除錯。
        saved = sorted(segments_dir.glob("segment-*.png"))
        assert len(saved) == 3


class TestControlledSegmentOverlap:
    def test_dark_first_row_of_each_segment_is_removed_by_controlled_overlap(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("native_capture.stitcher.time.sleep", lambda _s: None)
        driver = _DarkFirstRowFakeDriver()

        segments, images, _fixed_sticky = capture_segments(
            driver, page_height_css=10, viewport_height=4, segments_dir=tmp_path, settle_ms=0
        )

        assert [segment["actual_scroll_y"] for segment in segments] == [0, 3, 6]
        canvas = stitch(images, segments, dpr=1, page_height_css=10)

        # 每段第 0 列都故意暗化；接縫應使用受控重疊裁掉這些列。
        assert canvas.getpixel((0, 4)) == (30, 30, 30)
        assert canvas.getpixel((0, 7)) == (60, 60, 60)

    def test_overlap_crop_scales_actual_scroll_y_with_dpr(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("native_capture.stitcher.time.sleep", lambda _s: None)
        driver = _DarkFirstRowFakeDriver(dpr=2)
        segments, images, _fixed_sticky = capture_segments(
            driver, page_height_css=10, viewport_height=4, segments_dir=tmp_path, settle_ms=0
        )

        # actual scrollY 的 CSS 重疊要換算成 2 倍物理像素後再裁切。
        assert [segment["actual_scroll_y"] for segment in segments] == [0, 3, 6]
        canvas = stitch(images, segments, dpr=2, page_height_css=10)
        assert canvas.getpixel((0, 8)) == (30, 30, 30)
        assert canvas.getpixel((0, 14)) == (60, 60, 60)


class _DarkFirstRowFakeDriver:
    """模擬 Safari 每段截圖的第 0 列暗化，並回傳可辨識的實際 scrollY。"""

    def __init__(self, dpr: int = 1) -> None:
        self._scroll_y = 0
        self._dpr = dpr

    def execute_script(self, script: str, *args: object) -> object:
        if "window.scrollTo" in script:
            match = re.search(r"window\.scrollTo\(0,\s*(\d+)\)", script)
            assert match is not None
            self._scroll_y = int(match.group(1))
            return None
        if "window.scrollY" in script:
            return self._scroll_y
        if "document.documentElement.scrollHeight" in script:
            return 10
        if "innerWidth, window.innerHeight" in script:
            return [8, 4]
        if "data-native-capture-hidden" in script:
            return []
        if "removeProperty" in script:
            return None
        raise AssertionError(f"未預期的 script：{script}")

    def get_screenshot_as_png(self) -> bytes:
        colors = {
            0: (10, 10, 10),
            4: (40, 40, 40),
            3: (30, 30, 30),
            6: (60, 60, 60),
        }
        image = _dark_first_row_image(8 * self._dpr, 4 * self._dpr, colors[self._scroll_y])
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        return buffer.getvalue()


def _dark_first_row_image(width: int, height: int, color: tuple[int, int, int]) -> Image.Image:
    """建立第 0 列暗化、其餘列可辨識的測試圖片。"""
    image = Image.new("RGB", (width, height), color)
    for x in range(image.width):
        image.putpixel((x, 0), (200, 200, 200))
    return image
