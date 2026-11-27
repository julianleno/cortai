import importlib
import os
import tempfile
import unittest


class TestAppPersistence(unittest.TestCase):
    """Exercises the Flask app's job routes against real SQLite, including a
    simulated process restart (fresh app import against the same db file)."""

    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        os.environ["DB_PATH"] = os.path.join(self.test_dir, "cortai.db")
        os.environ["OUTPUT_DIR"] = os.path.join(self.test_dir, "output")
        os.environ["TEMP_DIR"] = os.path.join(self.test_dir, "temp")

    def tearDown(self):
        import shutil
        for key in ("DB_PATH", "OUTPUT_DIR", "TEMP_DIR"):
            os.environ.pop(key, None)
        shutil.rmtree(self.test_dir)

    def _fresh_app(self):
        # Re-import in dependency order so each "restart" re-reads env vars
        # and re-runs db.init_db against the same on-disk file. core.py does
        # `from .config import config`, so it must be reloaded too, or it
        # (and app.py, which imports config back out of core) would keep
        # holding the stale pre-test Config singleton.
        import cortai.config as config_module
        import cortai.core as core_module
        import cortai.app as app_module
        importlib.reload(config_module)
        importlib.reload(core_module)
        importlib.reload(app_module)
        app_module.db.init_db(app_module.config.DB_PATH)
        return app_module

    def test_job_created_by_one_process_visible_after_restart(self):
        app_module = self._fresh_app()
        from cortai import db

        job_id = "restart-e2e-job"
        db.create_job(app_module.config.DB_PATH, job_id, "https://youtu.be/test", "0", "5")
        db.update_job(app_module.config.DB_PATH, job_id, status="completed", progress=100)

        # Simulate the Flask process restarting: reload app + config fresh.
        restarted_app_module = self._fresh_app()
        client = restarted_app_module.app.test_client()

        response = client.get(f"/api/status/{job_id}")
        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertEqual(data["status"], "completed")
        self.assertEqual(data["progress"], 100)

    def test_status_route_404_for_unknown_job(self):
        app_module = self._fresh_app()
        client = app_module.app.test_client()

        response = client.get("/api/status/does-not-exist")
        self.assertEqual(response.status_code, 404)


if __name__ == "__main__":
    unittest.main()
