import os
import tempfile
import unittest
import urllib.request

import ffmpeg

from cortai.face_tracking import (
    FaceSample,
    build_speech_windows,
    compute_active_speaker_windows,
    compute_center_crop_window,
    compute_face_crop_window,
    compute_vertical_crop_window,
    sample_face_track,
)

MESSI_IMAGE_URL = "https://raw.githubusercontent.com/opencv/opencv/master/samples/data/messi5.jpg"


class TestCenterCropMath(unittest.TestCase):

    def test_landscape_source_crops_left_and_right(self):
        # 1920x1080 source, 9:16 target -> crop width, full height
        x, y, w, h = compute_center_crop_window(1920, 1080, target_width=1080, target_height=1920)
        self.assertEqual(h, 1080)
        self.assertEqual(w, 608)  # round(1080 * 9/16)
        self.assertEqual(y, 0)
        self.assertEqual(x, (1920 - 608) // 2)

    def test_already_vertical_source_crops_top_and_bottom(self):
        # A source already taller than 9:16 (e.g. 1080x2400) crops top/bottom, keeps full width.
        x, y, w, h = compute_center_crop_window(1080, 2400, target_width=1080, target_height=1920)
        self.assertEqual(w, 1080)
        self.assertEqual(x, 0)
        self.assertLess(h, 2400)

    def test_crop_window_stays_within_source_bounds(self):
        x, y, w, h = compute_center_crop_window(1920, 1080)
        self.assertGreaterEqual(x, 0)
        self.assertGreaterEqual(y, 0)
        self.assertLessEqual(x + w, 1920)
        self.assertLessEqual(y + h, 1080)


class TestFaceCropMath(unittest.TestCase):

    def test_empty_samples_falls_back_to_center_crop(self):
        result = compute_face_crop_window([], 1920, 1080)
        expected = compute_center_crop_window(1920, 1080)
        self.assertEqual(result, expected)

    def test_crop_centers_on_median_face_position(self):
        # Face consistently near x=1400 (right side of a 1920-wide frame).
        samples = [
            FaceSample(timestamp=i, x=1380 + i, y=300, w=200, h=200, confidence=0.9)
            for i in range(5)
        ]
        x, y, w, h = compute_face_crop_window(samples, 1920, 1080, target_width=1080, target_height=1920)
        median_cx = 1380 + 2 + 100  # median x (1382) + half width
        self.assertAlmostEqual(x + w / 2, median_cx, delta=2)

    def test_headroom_leaves_margin_above_head(self):
        samples = [FaceSample(timestamp=0, x=800, y=200, w=200, h=200, confidence=0.9)]
        x, y, w, h = compute_face_crop_window(samples, 1920, 1080, target_width=1080, target_height=1920,
                                               headroom_ratio=0.12)
        # Face top (200) should sit below the crop's top edge (y), leaving a headroom margin.
        self.assertLess(y, 200)
        self.assertGreater(200 - y, 0)

    def test_crop_window_clamped_when_face_near_edge(self):
        # Face very close to the left edge -- crop must not go negative.
        samples = [FaceSample(timestamp=0, x=5, y=5, w=50, h=50, confidence=0.9)]
        x, y, w, h = compute_face_crop_window(samples, 1920, 1080, target_width=1080, target_height=1920)
        self.assertGreaterEqual(x, 0)
        self.assertGreaterEqual(y, 0)
        self.assertLessEqual(x + w, 1920)
        self.assertLessEqual(y + h, 1080)


class TestNoFaceFallback(unittest.TestCase):
    """Runs the real detector (no mocking) against a video with no face at all."""

    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.no_face_video = os.path.join(self.test_dir, "no_face.mp4")
        (
            ffmpeg
            .input('testsrc=duration=3:size=640x360:rate=5', f='lavfi')
            .output(self.no_face_video, vcodec='libx264', pix_fmt='yuv420p', t=3)
            .overwrite_output()
            .run(capture_stdout=True, capture_stderr=True)
        )

    def tearDown(self):
        import shutil
        shutil.rmtree(self.test_dir)

    def test_sample_face_track_returns_empty_for_color_bars(self):
        samples = sample_face_track(self.no_face_video, num_samples=5)
        self.assertEqual(samples, [])

    def test_compute_vertical_crop_window_uses_fallback(self):
        window = compute_vertical_crop_window(self.no_face_video, target_width=200, target_height=356, num_samples=5)
        self.assertTrue(window["used_fallback"])
        self.assertEqual(window["faces_detected"], 0)
        self.assertGreater(window["width"], 0)
        self.assertGreater(window["height"], 0)


class TestRealFaceDetectionLive(unittest.TestCase):
    """Downloads OpenCV's own well-known sample face image (used across their
    official face-detection tutorials) to prove the detector finds a real
    face, not just synthetic bounding boxes. Skipped if network is
    unavailable, matching the pattern used for the live Gemini/NVIDIA tests."""

    @classmethod
    def setUpClass(cls):
        cls.test_dir = tempfile.mkdtemp()
        cls.image_path = os.path.join(cls.test_dir, "messi5.jpg")
        try:
            urllib.request.urlretrieve(MESSI_IMAGE_URL, cls.image_path)
        except Exception as exc:
            raise unittest.SkipTest(f"Sem acesso de rede para baixar imagem de teste: {exc}")

        cls.face_video = os.path.join(cls.test_dir, "face.mp4")
        (
            ffmpeg
            .input(cls.image_path, loop=1, framerate=5)
            .output(cls.face_video, vcodec='libx264', pix_fmt='yuv420p', t=2)
            .overwrite_output()
            .run(capture_stdout=True, capture_stderr=True)
        )

    @classmethod
    def tearDownClass(cls):
        import shutil
        shutil.rmtree(cls.test_dir, ignore_errors=True)

    def test_detects_real_face_with_reasonable_confidence(self):
        samples = sample_face_track(self.face_video, num_samples=5)
        self.assertGreaterEqual(len(samples), 1)
        for sample in samples:
            self.assertGreaterEqual(sample.confidence, 0.7)

    def test_vertical_crop_window_does_not_fallback_when_face_present(self):
        window = compute_vertical_crop_window(self.face_video, target_width=200, target_height=356, num_samples=5)
        self.assertFalse(window["used_fallback"])
        self.assertGreaterEqual(window["faces_detected"], 1)


def _make_words(specs):
    """specs: list of (word, start, end)"""
    return [{"word": w, "start": s, "end": e, "probability": 0.99} for w, s, e in specs]


class TestBuildSpeechWindows(unittest.TestCase):
    """Pure windowing logic behind active speaker detection (Fase 9) --
    no video, no cv2, no network."""

    def test_no_boundaries_yields_single_window(self):
        words = _make_words([(f"w{i}", i * 0.3, i * 0.3 + 0.2) for i in range(5)])
        windows = build_speech_windows(words, boundary_indices=set(), total_duration=1.4, min_window_duration=3.0)
        self.assertEqual(windows, [(0.0, 1.4)])

    def test_splits_at_boundary_when_long_enough(self):
        words = _make_words([
            ("A", 0.0, 1.0), ("B", 1.0, 2.0), ("C.", 2.0, 4.0),   # window 1: 0-4s (boundary at idx 2)
            ("D", 4.0, 5.0), ("E", 5.0, 6.0), ("F.", 6.0, 8.0),   # window 2: 4-8s (boundary at idx 5, last word)
        ])
        windows = build_speech_windows(words, boundary_indices={2, 5}, total_duration=8.0, min_window_duration=3.0)
        self.assertEqual(windows, [(0.0, 4.0), (4.0, 8.0)])

    def test_boundary_too_early_is_skipped_for_min_duration(self):
        # Boundary at idx 0 (0.0-0.5s) is far too short; only the final
        # boundary (last word) should produce a window.
        words = _make_words([("A.", 0.0, 0.5), ("B", 0.5, 2.0), ("C.", 2.0, 5.0)])
        windows = build_speech_windows(words, boundary_indices={0, 2}, total_duration=5.0, min_window_duration=3.0)
        self.assertEqual(windows, [(0.0, 5.0)])

    def test_short_trailing_window_merges_into_previous(self):
        words = _make_words([
            ("A", 0.0, 1.0), ("B", 1.0, 2.0), ("C.", 2.0, 4.0),  # 0-4s
            ("D.", 4.0, 4.3),                                     # 4-4.3s: too short alone
        ])
        windows = build_speech_windows(words, boundary_indices={2, 3}, total_duration=4.3, min_window_duration=3.0)
        self.assertEqual(windows, [(0.0, 4.3)])

    def test_windows_are_contiguous_and_boundaries_never_fall_mid_word(self):
        words = _make_words([(f"w{i}", i * 1.0, i * 1.0 + 0.8) for i in range(10)])
        boundary_indices = {2, 5, 9}
        windows = build_speech_windows(words, boundary_indices, total_duration=9.8, min_window_duration=1.0)

        # Contiguous: no gaps, no overlaps.
        self.assertAlmostEqual(windows[0][0], 0.0)
        for a, b in zip(windows, windows[1:]):
            self.assertAlmostEqual(a[1], b[0])
        self.assertAlmostEqual(windows[-1][1], 9.8)

        # Every internal cut is a real word boundary (word end), never mid-word.
        word_ends = {w["end"] for w in words}
        for _, end in windows[:-1]:
            self.assertIn(end, word_ends)

    def test_pause_and_trailing_silence_are_absorbed_not_dropped(self):
        """A real pause between boundary words (silence, not just the next
        word starting immediately) and trailing silence after the last word
        must both be folded into the surrounding windows -- if a gap were
        left out entirely, concatenating the cropped segments would shorten
        the video and desync captions from the rest of the pipeline."""
        words = _make_words([
            ("A", 0.0, 1.0), ("B.", 1.0, 3.0),   # window 1 ends here (boundary at idx 1)
            # 1.5s real pause/silence between 3.0 and 4.5 -- next word starts late
            ("C", 4.5, 5.5), ("D.", 5.5, 7.0),   # last word ends at 7.0
        ])
        total_duration = 8.2  # 1.2s of trailing silence after the last word
        windows = build_speech_windows(words, boundary_indices={1, 3}, total_duration=total_duration,
                                       min_window_duration=1.0)
        self.assertEqual(windows, [(0.0, 3.0), (3.0, 8.2)])
        # Confirm no gap and full coverage.
        self.assertAlmostEqual(windows[0][1], windows[1][0])
        self.assertAlmostEqual(windows[-1][1], total_duration)

    def test_empty_words_returns_empty(self):
        self.assertEqual(build_speech_windows([], set(), total_duration=0.0), [])


class TestComputeActiveSpeakerWindowsReal(unittest.TestCase):
    """Uses the real fixture transcript (Fase 2) combined with a real
    generated video to prove compute_active_speaker_windows produces
    multiple windows anchored on real speech boundaries."""

    @classmethod
    def setUpClass(cls):
        import json
        fixture_path = os.path.join(os.path.dirname(__file__), "fixtures", "flite_sample_transcript.json")
        with open(fixture_path, "r", encoding="utf-8") as f:
            cls.transcription = json.load(f)

        cls.test_dir = tempfile.mkdtemp()
        cls.video_path = os.path.join(cls.test_dir, "video.mp4")
        (
            ffmpeg
            .input('testsrc=duration=6:size=640x360:rate=5', f='lavfi')
            .output(cls.video_path, vcodec='libx264', t=6, pix_fmt='yuv420p')
            .overwrite_output()
            .run(capture_stdout=True, capture_stderr=True)
        )

    @classmethod
    def tearDownClass(cls):
        import shutil
        shutil.rmtree(cls.test_dir, ignore_errors=True)

    def test_produces_windows_covering_full_duration_without_gaps(self):
        windows = compute_active_speaker_windows(
            self.video_path, self.transcription, target_width=200, target_height=356,
            min_window_duration=1.0, samples_per_window=2,
        )
        self.assertGreaterEqual(len(windows), 1)
        # Contiguous: each window's end matches the next window's start.
        for a, b in zip(windows, windows[1:]):
            self.assertAlmostEqual(a["end"], b["start"], places=6)
        self.assertAlmostEqual(windows[0]["start"], 0.0, delta=1e-6)
        # Covers the full 6s test video, not just the 5.6s transcript --
        # trailing silence after the last word is folded into the last window.
        self.assertAlmostEqual(windows[-1]["end"], 6.0, delta=1e-6)


if __name__ == "__main__":
    unittest.main()
