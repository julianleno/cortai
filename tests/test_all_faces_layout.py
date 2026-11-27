import os
import tempfile
import unittest

import numpy as np

from cortai.face_tracking import (
    compute_panel_crop_window,
    compute_stacked_faces_crop_windows,
    FaceSample,
)


class TestAllFacesLayout(unittest.TestCase):
    """Testes unitários e de integração para o layout 'Manter todos os rostos' (empilhado / split-screen)."""

    def test_compute_panel_crop_window_aspect(self):
        # Source 1920x1080 (16:9), target panel 1080x960 (aspect 1.125)
        samples = [FaceSample(timestamp=0.0, x=200, y=200, w=100, h=100, confidence=0.9)]
        x, y, w, h = compute_panel_crop_window(samples, 1920, 1080, 1080, 960)

        # Expected panel aspect ratio = 1080 / 960 = 1.125
        self.assertAlmostEqual(w / h, 1.125, delta=0.01)
        self.assertGreaterEqual(x, 0)
        self.assertGreaterEqual(y, 0)
        self.assertLessEqual(x + w, 1920)
        self.assertLessEqual(y + h, 1080)

    def test_compute_stacked_faces_fallback_when_no_video(self):
        test_dir = tempfile.mkdtemp()
        try:
            # When video does not exist or has 0 faces, returns fallback True
            dummy_path = os.path.join(test_dir, "nonexistent.mp4")
            with self.assertRaises(ValueError):
                compute_stacked_faces_crop_windows(dummy_path)
        finally:
            import shutil
            shutil.rmtree(test_dir, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
