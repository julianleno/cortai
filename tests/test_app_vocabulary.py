import importlib
import json
import os
import tempfile
import unittest
from unittest import mock


class TestAppVocabulary(unittest.TestCase):
    """Fase 9: exercises the /submit vocabulary field and the
    /clips/<id>/edit-transcript manual-correction routes against real
    SQLite, mocking only the heavy download/transcribe/render steps --
    same pattern as test_app_templates.py."""

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
        import cortai.config as config_module
        import cortai.core as core_module
        import cortai.app as app_module
        importlib.reload(config_module)
        importlib.reload(core_module)
        importlib.reload(app_module)
        return app_module

    def _word(self, text, start, end):
        return {"word": text, "start": start, "end": end, "probability": 0.9}

    def test_submit_merges_template_and_form_vocabulary_into_job(self):
        app_module = self._fresh_app()
        from cortai import templates as template_config
        cfg = template_config.validate_template_config({"vocabulary": ["ANAC"]})
        template = app_module.db.create_template(app_module.config.DB_PATH, "tpl-voc", "Canal Voc", cfg)

        client = app_module.app.test_client()
        with mock.patch.object(app_module, 'process_suggestion_job'):
            response = client.post('/submit', data={
                'url': 'https://youtu.be/xyz',
                'min_duration': '15',
                'max_duration': '60',
                'num_clips': '3',
                'template_id': template['id'],
                'vocabulary': 'VOEPASS',
            }, follow_redirects=False)

        self.assertEqual(response.status_code, 302)
        jobs = app_module.db.list_jobs(app_module.config.DB_PATH)
        self.assertEqual(len(jobs), 1)
        self.assertCountEqual(jobs[0]['vocabulary'], ['ANAC', 'VOEPASS'])

    def test_submit_without_vocabulary_field_defaults_to_template_only(self):
        app_module = self._fresh_app()
        from cortai import templates as template_config
        cfg = template_config.validate_template_config({"vocabulary": ["ANAC"]})
        template = app_module.db.create_template(app_module.config.DB_PATH, "tpl-voc2", "Canal Voc2", cfg)

        client = app_module.app.test_client()
        with mock.patch.object(app_module, 'process_suggestion_job'):
            client.post('/submit', data={
                'url': 'https://youtu.be/xyz2',
                'min_duration': '15',
                'max_duration': '60',
                'num_clips': '3',
                'template_id': template['id'],
            }, follow_redirects=False)

        jobs = app_module.db.list_jobs(app_module.config.DB_PATH)
        self.assertEqual(jobs[0]['vocabulary'], ['ANAC'])

    def _make_job_and_clip_with_video(self, app_module, vocabulary):
        video_path = os.path.join(self.test_dir, "source.mp4")
        with open(video_path, "wb") as f:
            f.write(b"fake video bytes")

        job_id = "job-edit-1"
        app_module.db.create_job(app_module.config.DB_PATH, job_id, "https://youtu.be/x", "15", "60")
        app_module.db.update_job(app_module.config.DB_PATH, job_id,
                                  source_video_path=video_path,
                                  vocabulary_json=json.dumps(vocabulary))

        clip_id = "clip-edit-1"
        app_module.db.create_clip_suggestion(
            app_module.config.DB_PATH, clip_id, job_id, 0.0, 4.0,
            "Titulo", "Hook", "Justificativa", 8.0, "local")
        app_module.db.update_clip(app_module.config.DB_PATH, clip_id, status='rendered')
        return job_id, clip_id, video_path

    def test_edit_transcript_get_shows_vocabulary_corrected_words(self):
        app_module = self._fresh_app()
        job_id, clip_id, video_path = self._make_job_and_clip_with_video(app_module, ["ANAC"])

        fake_transcription = {
            "language": "pt", "segments": [{
                "id": 0, "start": 0.0, "end": 4.0, "text": " A ANAK informou.",
                "words": [
                    self._word(" A", 0.0, 0.5),
                    self._word(" ANAK", 0.5, 1.5),
                    self._word(" informou.", 1.5, 2.5),
                ],
            }],
        }

        client = app_module.app.test_client()
        with mock.patch.object(app_module.transcription_module, 'transcribe_video_file',
                                return_value=fake_transcription):
            response = client.get(f'/clips/{clip_id}/edit-transcript')

        self.assertEqual(response.status_code, 200)
        # The vocabulary correction (ANAK -> ANAC) must already be reflected
        # in the pre-filled word inputs shown to the user.
        self.assertIn(b'value="ANAC"', response.data)
        self.assertNotIn(b'value="ANAK"', response.data)

    def test_edit_transcript_post_saves_minimal_overrides_and_triggers_rerender(self):
        app_module = self._fresh_app()
        job_id, clip_id, video_path = self._make_job_and_clip_with_video(app_module, ["ANAC"])

        fake_transcription = {
            "language": "pt", "segments": [{
                "id": 0, "start": 0.0, "end": 4.0, "text": " A ANAK informou tudo.",
                "words": [
                    self._word(" A", 0.0, 0.5),
                    self._word(" ANAK", 0.5, 1.5),
                    self._word(" informou", 1.5, 2.2),
                    self._word(" tudo.", 2.2, 2.8),
                ],
            }],
        }

        client = app_module.app.test_client()
        rerender_calls = []

        def fake_rerender_clip_job(clip_id_arg, video_path_arg, template_id_arg):
            rerender_calls.append((clip_id_arg, video_path_arg, template_id_arg))

        with mock.patch.object(app_module.transcription_module, 'transcribe_video_file',
                                return_value=fake_transcription), \
             mock.patch.object(app_module, 'rerender_clip_job', side_effect=fake_rerender_clip_job):
            response = client.post(f'/clips/{clip_id}/edit-transcript', data={
                'word_0': 'A',
                'word_1': 'ANAC',       # already vocabulary-corrected -- should NOT become an override
                'word_2': 'informou',
                'word_3': 'tudinho.',   # user's manual correction -- SHOULD become an override
            }, follow_redirects=False)

        self.assertEqual(response.status_code, 302)
        clip = app_module.db.get_clip(app_module.config.DB_PATH, clip_id)
        self.assertEqual(clip['word_overrides'], {"3": "tudinho."})
        self.assertEqual(clip['status'], 'processing')
        self.assertEqual(len(rerender_calls), 1)
        self.assertEqual(rerender_calls[0][0], clip_id)
        self.assertEqual(rerender_calls[0][1], video_path)

    def test_edit_transcript_returns_404_style_redirect_when_video_missing(self):
        app_module = self._fresh_app()
        job_id = "job-missing-video"
        app_module.db.create_job(app_module.config.DB_PATH, job_id, "https://youtu.be/x", "15", "60")
        app_module.db.update_job(app_module.config.DB_PATH, job_id,
                                  source_video_path=os.path.join(self.test_dir, "does_not_exist.mp4"))
        clip_id = "clip-missing-video"
        app_module.db.create_clip_suggestion(
            app_module.config.DB_PATH, clip_id, job_id, 0.0, 4.0,
            "T", "H", "J", 5.0, "local")

        client = app_module.app.test_client()
        response = client.get(f'/clips/{clip_id}/edit-transcript', follow_redirects=True)
        self.assertEqual(response.status_code, 200)
        # Should redirect back to the status page, not raise/500.
        self.assertIn(job_id.encode(), response.request.path.encode())


if __name__ == "__main__":
    unittest.main()
