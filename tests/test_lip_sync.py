import unittest
import numpy as np

from cortai.face_tracking import (
    extract_mouth_roi,
    compute_lip_movement_score,
    FaceSample,
    FaceTrack,
)


class TestLipSyncAnalysis(unittest.TestCase):
    """Testes unitários para análise de movimento labial (lip-sync)."""

    def test_extract_mouth_roi_valid(self):
        # 100x100 synthetic frame
        frame = np.zeros((100, 100, 3), dtype=np.uint8)
        # Bounding box x=20, y=20, w=50, h=50
        # Landmarks: [right_eye, left_eye, nose_tip, right_mouth, left_mouth]
        landmarks = [
            30.0, 35.0,  # right eye
            60.0, 35.0,  # left eye
            45.0, 50.0,  # nose tip
            35.0, 65.0,  # right mouth corner
            55.0, 65.0,  # left mouth corner
        ]
        crop, width = extract_mouth_roi(frame, 20.0, 20.0, 50.0, 50.0, landmarks, target_size=(32, 32))
        self.assertIsNotNone(crop)
        self.assertEqual(crop.shape, (32, 32, 3))
        self.assertAlmostEqual(width, 20.0, delta=0.1)

    def test_compute_lip_movement_score_static_vs_moving(self):
        # Static mouth ROIs (no movement)
        static_roi = np.full((32, 32, 3), 128, dtype=np.uint8)
        static_rois = [static_roi, static_roi.copy(), static_roi.copy()]
        static_widths = [20.0, 20.0, 20.0]
        static_score = compute_lip_movement_score(static_rois, static_widths)

        # Moving mouth ROIs (lip movement / opening mouth)
        moving_rois = [
            np.full((32, 32, 3), 50, dtype=np.uint8),
            np.full((32, 32, 3), 200, dtype=np.uint8),
            np.full((32, 32, 3), 80, dtype=np.uint8),
        ]
        moving_widths = [18.0, 25.0, 19.0]
        moving_score = compute_lip_movement_score(moving_rois, moving_widths)

        self.assertEqual(static_score, 0.0)
        self.assertGreater(moving_score, 10.0, "Expected moving mouth to produce high lip movement score")

    def test_face_track_properties(self):
        track = FaceTrack(track_id=1)
        track.samples.append(FaceSample(timestamp=0.0, x=10, y=10, w=40, h=40, confidence=0.9))
        track.samples.append(FaceSample(timestamp=0.5, x=20, y=20, w=40, h=40, confidence=0.9))

        self.assertEqual(track.median_area, 1600.0)
        self.assertEqual(track.median_center, (35.0, 35.0))


if __name__ == "__main__":
    unittest.main()
