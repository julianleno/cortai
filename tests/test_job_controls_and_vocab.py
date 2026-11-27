import importlib
import os
import tempfile
import unittest
import uuid
from cortai import db


class TestJobControlsAndVocab(unittest.TestCase):

    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        os.environ["DB_PATH"] = os.path.join(self.test_dir, "cortai.db")
        os.environ["OUTPUT_DIR"] = os.path.join(self.test_dir, "output")
        os.environ["TEMP_DIR"] = os.path.join(self.test_dir, "temp")

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

        self.job_id = f"test-control-{uuid.uuid4().hex}"
        db.create_job(config_module.config.DB_PATH, self.job_id, "https://youtube.com/watch?v=12345", "15", "60")
        db.update_job(config_module.config.DB_PATH, self.job_id, status="processing")

    def tearDown(self):
        import shutil
        for key in ("DB_PATH", "OUTPUT_DIR", "TEMP_DIR"):
            os.environ.pop(key, None)
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_pause_resume_and_cancel_job(self):
        # 1. Pause job
        res_pause = self.client.post(f"/jobs/{self.job_id}/pause", follow_redirects=True)
        self.assertEqual(res_pause.status_code, 200)
        job = db.get_job(self.config_module.config.DB_PATH, self.job_id)
        self.assertEqual(job["status"], "paused")

        # 2. Resume job
        res_resume = self.client.post(f"/jobs/{self.job_id}/resume", follow_redirects=True)
        self.assertEqual(res_resume.status_code, 200)
        job = db.get_job(self.config_module.config.DB_PATH, self.job_id)
        self.assertEqual(job["status"], "processing")

        # 3. Cancel job while preserving its audit/history record.
        res_cancel = self.client.post(f"/jobs/{self.job_id}/cancel", follow_redirects=True)
        self.assertEqual(res_cancel.status_code, 200)
        job = db.get_job(self.config_module.config.DB_PATH, self.job_id)
        self.assertIsNotNone(job)
        self.assertEqual(job["status"], "cancelled")


if __name__ == "__main__":
    unittest.main()
