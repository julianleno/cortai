import importlib
import os
import tempfile
import unittest
from unittest import mock


class TestAppTemplates(unittest.TestCase):
    """Exercises the Flask template CRUD routes against real SQLite."""

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
        # Same reload order as test_app_persistence.py: core.py holds its own
        # `config` reference (`from .config import config`), so config, core,
        # and app must all be reloaded, in that dependency order.
        import cortai.config as config_module
        import cortai.core as core_module
        import cortai.app as app_module
        importlib.reload(config_module)
        importlib.reload(core_module)
        importlib.reload(app_module)
        app_module.db.init_db(app_module.config.DB_PATH)
        return app_module

    def test_templates_list_empty(self):
        app_module = self._fresh_app()
        client = app_module.app.test_client()
        response = client.get('/templates')
        self.assertEqual(response.status_code, 200)

    def test_templates_list_uses_caption_preset_metadata(self):
        app_module = self._fresh_app()
        from cortai import templates as template_config

        for preset in ("spoken-word-pop", "white-box", "cyan-shadow"):
            with self.subTest(preset=preset):
                cfg = template_config.validate_template_config(
                    {"caption_preset": preset}
                )
                app_module.db.create_template(
                    app_module.config.DB_PATH, "tpl-" + preset, "Example " + preset, cfg
                )

        response = app_module.app.test_client().get('/templates')
        self.assertEqual(response.status_code, 200)
        text = response.get_data(as_text=True)
        for preset in ("spoken-word-pop", "white-box", "cyan-shadow"):
            label = app_module.CAPTION_PRESETS_METADATA[preset]["label"]
            self.assertIn(label, text)

    def test_templates_list_preserves_unknown_caption_preset(self):
        app_module = self._fresh_app()
        from cortai import templates as template_config

        cfg = template_config.validate_template_config({})
        cfg["caption_preset"] = "future-preset"
        app_module.db.create_template(
            app_module.config.DB_PATH, "tpl-future", "Future template", cfg
        )

        response = app_module.app.test_client().get('/templates')
        self.assertEqual(response.status_code, 200)
        self.assertIn("future-preset", response.get_data(as_text=True))

    def test_create_template_via_form(self):
        app_module = self._fresh_app()
        client = app_module.app.test_client()

        response = client.post('/templates/new', data={
            'name': 'Meu Canal',
            'layout': 'center',
            'caption_preset': 'cyan-shadow',
            'watermark_enabled': 'on',
            'watermark_position': 'top-left',
            'watermark_opacity': '0.8',
            'watermark_margin_x': '10',
            'watermark_margin_y': '10',
            'watermark_width': '',
            'title': 'Meu Canal Incrivel',
            'cta': 'Segue la!',
        }, follow_redirects=True)

        self.assertEqual(response.status_code, 200)
        templates = app_module.db.list_templates(app_module.config.DB_PATH)
        self.assertEqual(len(templates), 1)
        self.assertEqual(templates[0]['name'], 'Meu Canal')
        self.assertEqual(templates[0]['config']['layout'], 'center')
        self.assertEqual(templates[0]['config']['caption_preset'], 'cyan-shadow')
        self.assertTrue(templates[0]['config']['watermark']['enabled'])
        self.assertEqual(templates[0]['config']['title'], 'Meu Canal Incrivel')

    def test_create_template_with_invalid_config_is_rejected(self):
        app_module = self._fresh_app()
        client = app_module.app.test_client()

        response = client.post('/templates/new', data={
            'name': 'Invalido',
            'layout': 'auto',
            'caption_preset': 'white-bold',
            'watermark_position': 'bottom-right',
            'watermark_opacity': '5.0',  # out of range
            'watermark_margin_x': '20',
            'watermark_margin_y': '20',
        }, follow_redirects=True)

        self.assertEqual(response.status_code, 200)
        templates = app_module.db.list_templates(app_module.config.DB_PATH)
        self.assertEqual(templates, [])

    def test_edit_template_updates_config(self):
        app_module = self._fresh_app()
        client = app_module.app.test_client()

        from cortai import templates as template_config
        cfg = template_config.validate_template_config({
            "caption_preset": "yellow-bold",
            "title": "Titulo preservado",
            "cta": "CTA preservado",
            "export": {"crf": 19},
        })
        template = app_module.db.create_template(app_module.config.DB_PATH, "tpl-edit", "Original", cfg)

        response = client.post(f'/templates/{template["id"]}/edit', data={
            'name': 'Renomeado',
            'layout': 'auto',
            'caption_preset': 'white-red-highlight',
            'watermark_position': 'bottom-right',
            'watermark_opacity': '0.5',
            'watermark_margin_x': '20',
            'watermark_margin_y': '20',
        }, follow_redirects=True)

        self.assertEqual(response.status_code, 200)
        updated = app_module.db.get_template(app_module.config.DB_PATH, "tpl-edit")
        self.assertEqual(updated['name'], 'Renomeado')
        self.assertEqual(updated['config']['caption_preset'], 'white-red-highlight')
        self.assertEqual(updated['config']['title'], 'Titulo preservado')
        self.assertEqual(updated['config']['cta'], 'CTA preservado')
        self.assertEqual(updated['config']['export']['crf'], 19)

    def test_delete_template_removes_it(self):
        app_module = self._fresh_app()
        client = app_module.app.test_client()

        from cortai import templates as template_config
        cfg = template_config.validate_template_config({})
        template = app_module.db.create_template(app_module.config.DB_PATH, "tpl-del", "Para Apagar", cfg)

        response = client.post(f'/templates/{template["id"]}/delete', follow_redirects=True)
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(app_module.db.get_template(app_module.config.DB_PATH, "tpl-del"))

    def test_index_lists_available_templates_in_selector(self):
        app_module = self._fresh_app()
        from cortai import templates as template_config
        cfg = template_config.validate_template_config({})
        app_module.db.create_template(app_module.config.DB_PATH, "tpl-idx", "Canal Principal", cfg)

        client = app_module.app.test_client()
        response = client.get('/')
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'Canal Principal', response.data)

    def test_process_suggestion_job_passes_render_kwargs_to_pipeline(self):
        """Fase 7: process_suggestion_job orchestrates download -> transcribe
        -> suggest_clips -> render_clip_from_source. Mocks the three
        network/heavy steps to isolate the orchestration + template wiring."""
        app_module = self._fresh_app()
        from cortai import templates as template_config
        cfg = template_config.validate_template_config({
            "caption_preset": "cyan-shadow",
            "layout": "center",
            "title": "Ola",
        })
        template = app_module.db.create_template(app_module.config.DB_PATH, "tpl-submit", "Canal X", cfg)

        fake_transcription = {"language": "pt", "segments": []}
        fake_suggestions = [{
            "start": 1.0, "end": 5.0, "title": "T", "hook": "H",
            "justificativa": "J", "clip_score": 7.5, "source": "local",
        }]
        captured = {}

        def fake_render_clip_from_source(video_path, transcription, start, end, output_dir, output_filename, **kwargs):
            captured.update(kwargs)
            os.makedirs(output_dir, exist_ok=True)
            path = os.path.join(output_dir, output_filename)
            with open(path, "wb") as f:
                f.write(b"fake mp4 bytes")
            return path

        os.makedirs(app_module.config.OUTPUT_DIR, exist_ok=True)
        with mock.patch.object(app_module, 'download_video', return_value="/fake/video.mp4"), \
             mock.patch.object(app_module.transcription_module, 'transcribe_video_file', return_value=fake_transcription), \
             mock.patch.object(app_module.clip_selection, 'suggest_clips_for_video', return_value=fake_suggestions), \
             mock.patch.object(app_module.clip_selection, 'suggest_clips', return_value=fake_suggestions), \
             mock.patch.object(app_module, 'render_clip_from_source', side_effect=fake_render_clip_from_source):
            app_module.db.create_job(app_module.config.DB_PATH, "job-submit-test", "https://youtu.be/test", "15", "60")
            app_module.process_suggestion_job(
                "job-submit-test", "https://youtu.be/test", 15.0, 60.0, 3, None, template["id"])

        self.assertEqual(captured.get('caption_preset'), 'cyan-shadow')
        self.assertEqual(captured.get('layout'), 'center')
        self.assertEqual(captured.get('title'), 'Ola')

        job = app_module.db.get_job(app_module.config.DB_PATH, "job-submit-test")
        self.assertEqual(job['status'], 'completed')
        self.assertEqual(job['source_video_path'], '/fake/video.mp4')

        clips = app_module.db.list_clips_for_job(app_module.config.DB_PATH, "job-submit-test")
        self.assertEqual(len(clips), 1)
        self.assertEqual(clips[0]['status'], 'rendered')
        self.assertEqual(clips[0]['title'], 'T')
        self.assertEqual(clips[0]['clip_score'], 7.5)


if __name__ == "__main__":
    unittest.main()
