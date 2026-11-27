import importlib
import io
import os
import tempfile
import unittest
from unittest import mock
from cortai import db


class TestLocalVideoUpload(unittest.TestCase):
    """Testes unitários para a funcionalidade de upload de vídeo local (.mp4, .mkv, .mov, etc.)."""

    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.test_dir, "test.db")
        self.output_dir = os.path.join(self.test_dir, "output")
        self.downloads_dir = os.path.join(self.test_dir, "downloads")
        os.makedirs(self.output_dir, exist_ok=True)
        os.makedirs(self.downloads_dir, exist_ok=True)

        os.environ["DB_PATH"] = self.db_path
        os.environ["OUTPUT_DIR"] = self.output_dir
        os.environ["YTDLP_OUTDIR"] = self.downloads_dir

        import cortai.config as config_module
        import cortai.core as core_module
        import cortai.app as app_module
        importlib.reload(config_module)
        importlib.reload(core_module)
        importlib.reload(app_module)

        self.app_module = app_module
        self.config_module = config_module
        self.client = app_module.app.test_client()
        db.init_db(config_module.config.DB_PATH)

    def tearDown(self):
        import shutil
        for key in ("DB_PATH", "OUTPUT_DIR", "YTDLP_OUTDIR"):
            os.environ.pop(key, None)
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_submit_local_file_upload_saves_file_and_creates_job(self):
        fake_mp4_bytes = b"\x00\x00\x00\x1cftypisom\x00\x00\x02\x00isomiso2mp41"
        data = {
            "input_source": "file",
            "video_file": (io.BytesIO(fake_mp4_bytes), "sample_video.mp4"),
            "min_duration": "10",
            "max_duration": "30",
            "num_clips": "2",
        }

        with mock.patch.object(self.app_module.threading.Thread, "start"):
            response = self.client.post(
                "/submit", data=data, content_type="multipart/form-data", follow_redirects=False)

        self.assertEqual(response.status_code, 302)
        self.assertIn("/status/", response.headers["Location"])

        # Check job in database
        jobs = db.list_jobs(self.config_module.config.DB_PATH)
        self.assertEqual(len(jobs), 1)
        job = jobs[0]
        self.assertIn("sample_video.mp4", job["url"])
        self.assertIsNotNone(job["source_video_path"])
        self.assertTrue(os.path.exists(job["source_video_path"]))

    def test_submit_invalid_extension_flashes_error(self):
        data = {
            "input_source": "file",
            "video_file": (io.BytesIO(b"fake text"), "document.pdf"),
        }
        response = self.client.post("/submit", data=data, content_type="multipart/form-data", follow_redirects=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Formato de", response.data)
        jobs = db.list_jobs(self.config_module.config.DB_PATH)
        self.assertEqual(len(jobs), 0)


if __name__ == "__main__":
    unittest.main()
