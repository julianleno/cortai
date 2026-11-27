import importlib
import os
import tempfile
import unittest
from cortai import db, clip_selection


class TestSettingsAndProviders(unittest.TestCase):

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

    def tearDown(self):
        import shutil
        for key in ("DB_PATH", "OUTPUT_DIR", "TEMP_DIR"):
            os.environ.pop(key, None)
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_settings_get_and_set(self):
        db.set_setting(self.config_module.config.DB_PATH, "test_key", "test_val")
        val = db.get_setting(self.config_module.config.DB_PATH, "test_key")
        self.assertEqual(val, "test_val")

    def test_settings_route_get_and_post(self):
        response = self.client.get('/settings')
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'Configura', response.data)

        post_data = {
            'gemini_api_key': 'test-gemini-key',
            'openai_api_key': 'test-openai-key',
            'anthropic_api_key': 'test-anthropic-key',
            'nvidia_nim_api_key': 'test-nvidia-key'
        }
        res_post = self.client.post('/settings', data=post_data, follow_redirects=True)
        self.assertEqual(res_post.status_code, 200)
        self.assertEqual(db.get_setting(self.config_module.config.DB_PATH, 'gemini_api_key'), 'test-gemini-key')
        self.assertEqual(db.get_setting(self.config_module.config.DB_PATH, 'openai_api_key'), 'test-openai-key')

    def test_no_keys_falls_back_100_percent_local(self):
        # Clear DB settings table and env keys to simulate 100% offline local run
        for key in ('gemini_api_key', 'openai_api_key', 'anthropic_api_key', 'nvidia_nim_api_key'):
            db.set_setting(self.config_module.config.DB_PATH, key, "")

        orig_gemini = clip_selection.config.GEMINI_API_KEY
        orig_nvidia = clip_selection.config.NVIDIA_NIM_API_KEY
        orig_openai = os.environ.pop("OPENAI_API_KEY", None)
        orig_anthropic = os.environ.pop("ANTHROPIC_API_KEY", None)
        clip_selection.config.GEMINI_API_KEY = None
        clip_selection.config.NVIDIA_NIM_API_KEY = None

        try:
            fake_transcription = {
                "text": "Olá pessoal, este é um vídeo de teste para verificar o corte 100% local. Vamos continuar o assunto de tecnologia.",
                "language": "pt",
                "segments": [
                    {
                        "start": 0.0, "end": 5.0, "text": "Olá pessoal, este é um vídeo de teste.",
                        "words": [
                            {"word": "Olá", "start": 0.0, "end": 0.5},
                            {"word": "pessoal,", "start": 0.5, "end": 1.0},
                            {"word": "este", "start": 1.0, "end": 1.5},
                            {"word": "é", "start": 1.5, "end": 1.8},
                            {"word": "um", "start": 1.8, "end": 2.0},
                            {"word": "vídeo", "start": 2.0, "end": 2.5},
                            {"word": "de", "start": 2.5, "end": 2.7},
                            {"word": "teste.", "start": 2.7, "end": 3.2}
                        ]
                    }
                ]
            }
            suggestions = clip_selection.suggest_clips(
                fake_transcription, min_duration=1.0, max_duration=10.0, max_clips=2
            )
            self.assertTrue(len(suggestions) > 0)
            for clip in suggestions:
                self.assertEqual(clip['source'], 'local')
        finally:
            self.config_module.config.GEMINI_API_KEY = orig_gemini
            self.config_module.config.NVIDIA_NIM_API_KEY = orig_nvidia
            if orig_openai: os.environ["OPENAI_API_KEY"] = orig_openai
            if orig_anthropic: os.environ["ANTHROPIC_API_KEY"] = orig_anthropic


if __name__ == '__main__':
    unittest.main()
