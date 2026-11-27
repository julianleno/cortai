import os
import tempfile
import unittest

from cortai import db


class TestPersistence(unittest.TestCase):

    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.test_dir, "cortai.db")
        db.init_db(self.db_path)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.test_dir)

    def test_create_and_get_job(self):
        job = db.create_job(self.db_path, "job-1", "https://youtu.be/abc", "0", "10")
        self.assertEqual(job["status"], "queued")
        self.assertEqual(job["progress"], 0)

        fetched = db.get_job(self.db_path, "job-1")
        self.assertEqual(fetched["url"], "https://youtu.be/abc")

    def test_job_survives_new_connection_restart(self):
        db.create_job(self.db_path, "job-restart", "https://youtu.be/xyz", "0", "5")
        db.update_job(self.db_path, "job-restart", status="completed", progress=100)

        # Simulate a process restart: nothing but the db file on disk is reused.
        reopened_job = db.get_job(self.db_path, "job-restart")
        self.assertIsNotNone(reopened_job)
        self.assertEqual(reopened_job["status"], "completed")
        self.assertEqual(reopened_job["progress"], 100)

    def test_update_job_failed_records_error(self):
        db.create_job(self.db_path, "job-fail", "https://youtu.be/err", "0", "5")
        db.update_job(self.db_path, "job-fail", status="failed", error="boom")

        job = db.get_job(self.db_path, "job-fail")
        self.assertEqual(job["status"], "failed")
        self.assertEqual(job["error"], "boom")

    def test_list_jobs_orders_newest_first(self):
        db.create_job(self.db_path, "job-a", "https://youtu.be/a", "0", "5")
        db.create_job(self.db_path, "job-b", "https://youtu.be/b", "0", "5")

        jobs = db.list_jobs(self.db_path)
        ids = [job["id"] for job in jobs]
        self.assertEqual(ids, ["job-b", "job-a"])

    def test_create_and_list_clips_for_job(self):
        db.create_job(self.db_path, "job-clip", "https://youtu.be/c", "0", "5")
        db.create_clip(self.db_path, "clip-1", "job-clip", "clip_1.mp4", "/tmp/clip_1.mp4")

        clips = db.list_clips_for_job(self.db_path, "job-clip")
        self.assertEqual(len(clips), 1)
        self.assertEqual(clips[0]["result_file"], "clip_1.mp4")

    def test_get_job_missing_returns_none(self):
        self.assertIsNone(db.get_job(self.db_path, "does-not-exist"))

    def test_create_and_get_template(self):
        config = {"layout": "center-crop", "caption_style": "white-bold"}
        db.create_template(self.db_path, "tpl-1", "Meu Canal", config)

        template = db.get_template(self.db_path, "tpl-1")
        self.assertEqual(template["name"], "Meu Canal")
        self.assertEqual(template["config"], config)

        templates = db.list_templates(self.db_path)
        self.assertEqual(len(templates), 1)

    def test_update_template_name_and_config(self):
        db.create_template(self.db_path, "tpl-2", "Original", {"layout": "auto"})

        updated = db.update_template(self.db_path, "tpl-2", name="Renomeado", config={"layout": "center"})
        self.assertEqual(updated["name"], "Renomeado")
        self.assertEqual(updated["config"], {"layout": "center"})

        fetched = db.get_template(self.db_path, "tpl-2")
        self.assertEqual(fetched["name"], "Renomeado")
        self.assertEqual(fetched["config"], {"layout": "center"})

    def test_update_template_partial_field_only(self):
        db.create_template(self.db_path, "tpl-3", "Nome", {"layout": "auto"})
        db.update_template(self.db_path, "tpl-3", name="Novo Nome")

        fetched = db.get_template(self.db_path, "tpl-3")
        self.assertEqual(fetched["name"], "Novo Nome")
        self.assertEqual(fetched["config"], {"layout": "auto"})  # untouched

    def test_delete_template_removes_it(self):
        db.create_template(self.db_path, "tpl-4", "Para Apagar", {})
        db.delete_template(self.db_path, "tpl-4")

        self.assertIsNone(db.get_template(self.db_path, "tpl-4"))
        self.assertEqual(db.list_templates(self.db_path), [])

    def test_create_and_update_clip_suggestion(self):
        db.create_job(self.db_path, "job-sugg", "https://youtu.be/x", "0", "0")
        clip = db.create_clip_suggestion(
            self.db_path, "clip-sugg-1", "job-sugg", 10.5, 25.0,
            "Titulo", "Gancho inicial", "Justificativa", 8.5, "gemini",
        )
        self.assertEqual(clip["status"], "pending")
        self.assertIsNone(clip["result_path"])
        self.assertEqual(clip["start_seconds"], 10.5)

        updated = db.update_clip(self.db_path, "clip-sugg-1", status="rendered",
                                  result_file="clip.mp4", result_path="/out/clip.mp4")
        self.assertEqual(updated["status"], "rendered")
        self.assertEqual(updated["result_file"], "clip.mp4")

        clips = db.list_clips_for_job(self.db_path, "job-sugg")
        self.assertEqual(len(clips), 1)
        self.assertEqual(clips[0]["title"], "Titulo")

    def test_update_clip_missing_returns_none(self):
        self.assertIsNone(db.update_clip(self.db_path, "does-not-exist", status="failed"))

    def test_init_db_migrates_old_schema_clips_table(self):
        """Simulates a dev database created before Fase 7 (clips table
        without the new suggestion columns) and confirms init_db() adds
        them without breaking existing rows."""
        old_db_path = os.path.join(self.test_dir, "old_schema.db")
        with_old_schema = __import__("sqlite3").connect(old_db_path)
        with_old_schema.executescript("""
            CREATE TABLE jobs (
                id TEXT PRIMARY KEY, url TEXT NOT NULL, start_time TEXT, end_time TEXT,
                status TEXT NOT NULL, progress INTEGER NOT NULL DEFAULT 0, error TEXT,
                created_at REAL NOT NULL, updated_at REAL NOT NULL
            );
            CREATE TABLE clips (
                id TEXT PRIMARY KEY, job_id TEXT NOT NULL, result_file TEXT NOT NULL,
                result_path TEXT NOT NULL, created_at REAL NOT NULL
            );
        """)
        with_old_schema.execute(
            "INSERT INTO clips (id, job_id, result_file, result_path, created_at) VALUES (?, ?, ?, ?, ?)",
            ("old-clip", "old-job", "old.mp4", "/out/old.mp4", 123.0),
        )
        with_old_schema.commit()
        with_old_schema.close()

        db.init_db(old_db_path)  # should migrate in place, not raise

        migrated = db.get_clip(old_db_path, "old-clip")
        self.assertEqual(migrated["result_file"], "old.mp4")
        self.assertEqual(migrated["status"], "rendered")  # migration default for pre-existing rows
        self.assertIsNone(migrated["start_seconds"])

        # New job column should also exist and accept writes.
        db.create_job(old_db_path, "new-job", "https://youtu.be/y", "0", "0")
        db.update_job(old_db_path, "new-job", source_video_path="/downloads/video.mp4")
        self.assertEqual(db.get_job(old_db_path, "new-job")["source_video_path"], "/downloads/video.mp4")

    def test_job_vocabulary_defaults_to_empty_list(self):
        job = db.create_job(self.db_path, "job-vocab-1", "https://youtu.be/x", "0", "0")
        self.assertEqual(job["vocabulary"], [])

    def test_job_vocabulary_round_trips_through_update(self):
        import json
        db.create_job(self.db_path, "job-vocab-2", "https://youtu.be/x", "0", "0")
        db.update_job(self.db_path, "job-vocab-2", vocabulary_json=json.dumps(["ANAC", "VOEPASS"]))

        fetched = db.get_job(self.db_path, "job-vocab-2")
        self.assertEqual(fetched["vocabulary"], ["ANAC", "VOEPASS"])
        listed = db.list_jobs(self.db_path)
        self.assertEqual(next(j for j in listed if j["id"] == "job-vocab-2")["vocabulary"], ["ANAC", "VOEPASS"])

    def test_clip_word_overrides_defaults_to_empty_dict(self):
        db.create_job(self.db_path, "job-wo", "https://youtu.be/x", "0", "0")
        clip = db.create_clip_suggestion(self.db_path, "clip-wo-1", "job-wo", 0.0, 5.0, "T", "H", "J", 5.0, "local")
        self.assertEqual(clip["word_overrides"], {})

    def test_clip_word_overrides_round_trip_through_update(self):
        import json
        db.create_job(self.db_path, "job-wo2", "https://youtu.be/x", "0", "0")
        db.create_clip_suggestion(self.db_path, "clip-wo-2", "job-wo2", 0.0, 5.0, "T", "H", "J", 5.0, "local")
        db.update_clip(self.db_path, "clip-wo-2", word_overrides_json=json.dumps({"3": "ANAC"}))

        fetched = db.get_clip(self.db_path, "clip-wo-2")
        self.assertEqual(fetched["word_overrides"], {"3": "ANAC"})
        listed = db.list_clips_for_job(self.db_path, "job-wo2")
        self.assertEqual(listed[0]["word_overrides"], {"3": "ANAC"})


if __name__ == "__main__":
    unittest.main()
