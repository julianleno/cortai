import importlib
import os
import tempfile
import unittest
from unittest import mock


class TestAppGallery(unittest.TestCase):
    """Fase 7: clip gallery, download, and re-render routes."""

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
        app_module.db.init_db(app_module.config.DB_PATH)
        return app_module

    def test_status_page_shows_clip_gallery(self):
        app_module = self._fresh_app()
        app_module.db.create_job(app_module.config.DB_PATH, "job-1", "https://youtu.be/x", "15", "60")
        app_module.db.update_job(app_module.config.DB_PATH, "job-1", status="completed", progress=100)
        app_module.db.create_clip_suggestion(
            app_module.config.DB_PATH, "clip-1", "job-1", 1.0, 10.0,
            "Titulo Legal", "Gancho forte", "Boa justificativa", 8.2, "gemini",
        )
        app_module.db.update_clip(app_module.config.DB_PATH, "clip-1", status="rendered",
                                  result_file="clip.mp4", result_path="/fake/clip.mp4")

        client = app_module.app.test_client()
        response = client.get('/status/job-1')
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'Titulo Legal', response.data)
        self.assertIn(b'Gancho forte', response.data)

    def test_api_status_includes_clips(self):
        app_module = self._fresh_app()
        app_module.db.create_job(app_module.config.DB_PATH, "job-2", "https://youtu.be/x", "15", "60")
        app_module.db.create_clip_suggestion(
            app_module.config.DB_PATH, "clip-2", "job-2", 0.0, 5.0,
            "T", "H", "J", 5.0, "local",
        )

        client = app_module.app.test_client()
        response = client.get('/api/status/job-2')
        data = response.get_json()
        self.assertEqual(len(data['clips']), 1)
        self.assertEqual(data['clips'][0]['id'], 'clip-2')
        self.assertEqual(data['clips'][0]['status'], 'pending')

    def test_download_clip_not_ready_redirects_with_flash(self):
        app_module = self._fresh_app()
        app_module.db.create_job(app_module.config.DB_PATH, "job-3", "https://youtu.be/x", "15", "60")
        app_module.db.create_clip_suggestion(
            app_module.config.DB_PATH, "clip-3", "job-3", 0.0, 5.0, "T", "H", "J", 5.0, "local",
        )

        client = app_module.app.test_client()
        response = client.get('/clips/clip-3/download', follow_redirects=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'ainda n\xc3\xa3o est\xc3\xa1 pronto', response.data)

    def test_download_clip_rendered_serves_file(self):
        app_module = self._fresh_app()
        app_module.db.create_job(app_module.config.DB_PATH, "job-4", "https://youtu.be/x", "15", "60")
        os.makedirs(app_module.config.OUTPUT_DIR, exist_ok=True)
        real_file = os.path.join(app_module.config.OUTPUT_DIR, "real_clip.mp4")
        with open(real_file, "wb") as f:
            f.write(b"fake video bytes")

        app_module.db.create_clip_suggestion(
            app_module.config.DB_PATH, "clip-4", "job-4", 0.0, 5.0, "T", "H", "J", 5.0, "local",
        )
        app_module.db.update_clip(app_module.config.DB_PATH, "clip-4", status="rendered",
                                  result_file="real_clip.mp4", result_path=real_file)

        client = app_module.app.test_client()
        response = client.get('/clips/clip-4/download')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, b"fake video bytes")

    def test_rerender_clip_reuses_cached_transcription_not_redownload(self):
        """The key Fase 7 acceptance criterion: re-rendering with a new
        template must not re-download or re-transcribe the source video."""
        app_module = self._fresh_app()

        os.makedirs(app_module.config.OUTPUT_DIR, exist_ok=True)
        fake_video_path = os.path.join(self.test_dir, "source.mp4")
        with open(fake_video_path, "wb") as f:
            f.write(b"fake source video")

        app_module.db.create_job(app_module.config.DB_PATH, "job-5", "https://youtu.be/x", "15", "60")
        app_module.db.update_job(app_module.config.DB_PATH, "job-5", source_video_path=fake_video_path)
        app_module.db.create_clip_suggestion(
            app_module.config.DB_PATH, "clip-5", "job-5", 1.0, 10.0, "T", "H", "J", 5.0, "local",
        )

        from cortai import templates as template_config
        cfg = template_config.validate_template_config({"caption_preset": "yellow-bold"})
        template = app_module.db.create_template(app_module.config.DB_PATH, "tpl-rerender", "Outro Canal", cfg)

        fake_transcription = {"language": "pt", "segments": []}

        def fake_render(video_path, transcription, start, end, output_dir, output_filename, **kwargs):
            path = os.path.join(output_dir, output_filename)
            with open(path, "wb") as f:
                f.write(b"rerendered bytes")
            return path

        with mock.patch.object(app_module, 'download_video') as mock_download, \
             mock.patch.object(app_module.transcription_module, 'transcribe_video_file',
                              return_value=fake_transcription) as mock_transcribe, \
             mock.patch.object(app_module, 'render_clip_from_source', side_effect=fake_render) as mock_render:
            app_module.rerender_clip_job("clip-5", fake_video_path, template["id"])

        mock_download.assert_not_called()
        mock_transcribe.assert_called_once()
        mock_render.assert_called_once()

        clip = app_module.db.get_clip(app_module.config.DB_PATH, "clip-5")
        self.assertEqual(clip['status'], 'rendered')

    def test_rerender_route_without_source_video_flashes_error(self):
        app_module = self._fresh_app()
        app_module.db.create_job(app_module.config.DB_PATH, "job-6", "https://youtu.be/x", "15", "60")
        app_module.db.create_clip_suggestion(
            app_module.config.DB_PATH, "clip-6", "job-6", 0.0, 5.0, "T", "H", "J", 5.0, "local",
        )

        client = app_module.app.test_client()
        response = client.post('/clips/clip-6/rerender', data={'template_id': ''}, follow_redirects=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn('não está mais disponível'.encode('utf-8'), response.data)


if __name__ == "__main__":
    unittest.main()
