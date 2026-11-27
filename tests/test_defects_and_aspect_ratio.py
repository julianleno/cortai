import io
import os
import tempfile
import unittest
from unittest import mock

from cortai import app as app_module
from cortai import db
from cortai.clip_selection import local_candidate_to_suggestion
from cortai.config import config
from cortai.face_tracking import FaceSample, compute_face_crop_window
from cortai.segmentation import ClipCandidate
from cortai.subtitles import build_ass_subtitle


class TestDefectsAndAspectRatio(unittest.TestCase):

    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.test_dir, "test.db")
        self.output_dir = os.path.join(self.test_dir, "output")
        self.downloads_dir = os.path.join(self.test_dir, "downloads")
        os.makedirs(self.output_dir, exist_ok=True)
        os.makedirs(self.downloads_dir, exist_ok=True)

        self._orig_db = config.DB_PATH
        config.DB_PATH = self.db_path
        db.init_db(config.DB_PATH)

        app_module.app.config["TESTING"] = True
        app_module.app.config["SECRET_KEY"] = "test-key"
        self.client = app_module.app.test_client()

        self._orig_out_dir = config.OUTPUT_DIR
        self._orig_ytdlp_dir = config.YTDLP_OUTDIR
        config.OUTPUT_DIR = self.output_dir
        config.YTDLP_OUTDIR = self.downloads_dir

    def tearDown(self):
        config.DB_PATH = self._orig_db
        config.OUTPUT_DIR = self._orig_out_dir
        config.YTDLP_OUTDIR = self._orig_ytdlp_dir
        os.makedirs(os.path.dirname(self._orig_db), exist_ok=True)
        db.init_db(self._orig_db)
        import shutil
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_subtitle_wrap_style_is_zero_and_margins_safe(self):
        transcription = {
            "segments": [
                {
                    "start": 0.0,
                    "end": 2.0,
                    "text": "Esta e uma frase bem longa para testar a quebra automatica",
                    "words": [
                        {"word": "Esta", "start": 0.0, "end": 0.3},
                        {"word": "uma", "start": 0.3, "end": 0.6},
                        {"word": "frase", "start": 0.6, "end": 0.9},
                        {"word": "bem", "start": 0.9, "end": 1.2},
                        {"word": "longa", "start": 1.2, "end": 1.5},
                        {"word": "para", "start": 1.5, "end": 1.7},
                        {"word": "testar", "start": 1.7, "end": 2.0},
                    ],
                }
            ]
        }
        ass_path = os.path.join(self.test_dir, "test.ass")
        build_ass_subtitle(transcription, "white-bold", 1080, 1920, ass_path, position="bottom")

        with open(ass_path, "r", encoding="utf-8") as f:
            content = f.read()

        self.assertIn("WrapStyle: 0", content)
        self.assertIn(",86,86,230,1", content)  # MarginL=86, MarginR=86, MarginV=230

    def test_face_crop_window_prevents_cutting_face_off(self):
        # Face located near the right boundary (x=1200, w=300 in 1920x1080 frame)
        samples = [FaceSample(timestamp=0.0, x=1200, y=200, w=300, h=300, confidence=0.9)]
        x, y, crop_w, crop_h = compute_face_crop_window(samples, 1920, 1080, 1080, 1920)

        # The left boundary of face is x=1200. x must be <= 1200 so face left is inside crop!
        self.assertLessEqual(x, 1200)
        # The right boundary of face is x=1500. x + crop_w must be >= 1500 so face right is inside crop!
        self.assertGreaterEqual(x + crop_w, 1500)

    def test_local_candidate_title_is_readable_not_generic(self):
        candidate = ClipCandidate(start=0.0, end=15.0, text="Como criar um canal de cortes no youtube", word_start_index=0, word_end_index=7)
        suggestion = local_candidate_to_suggestion(candidate)
        self.assertNotEqual(suggestion["title"], "Trecho selecionado automaticamente")
        self.assertIn("Clipe: Como criar um canal de", suggestion["title"])

    def test_aspect_ratio_9_16_enforces_60s_max_duration(self):
        data = {
            "input_source": "file",
            "video_file": (io.BytesIO(b"\x00\x00\x00\x1cftypisom"), "test.mp4"),
            "aspect_ratio": "9:16",
            "min_duration": "10",
            "max_duration": "120",  # Invalid > 60 for 9:16
        }
        response = self.client.post("/submit", data=data, content_type="multipart/form-data", follow_redirects=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Para o formato 9:16", response.data)

    def test_aspect_ratio_16_9_allows_up_to_300s_duration(self):
        data = {
            "input_source": "file",
            "video_file": (io.BytesIO(b"\x00\x00\x00\x1cftypisom"), "test.mp4"),
            "aspect_ratio": "16:9",
            "min_duration": "30",
            "max_duration": "240",  # Valid <= 300 for 16:9
        }
        with mock.patch.object(app_module.threading.Thread, "start"):
            response = self.client.post(
                "/submit", data=data, content_type="multipart/form-data", follow_redirects=False)
        self.assertEqual(response.status_code, 302)


if __name__ == "__main__":
    unittest.main()
