import io
import os
import tempfile
import unittest

from cortai import app as app_module
from cortai import db
from cortai.config import config
from cortai.subtitles import CAPTION_PRESETS, CAPTION_PRESETS_METADATA


class TestCaptionPresetsUI(unittest.TestCase):

    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.test_dir, "test.db")
        self.output_dir = os.path.join(self.test_dir, "output")
        self.downloads_dir = os.path.join(self.test_dir, "downloads")
        os.makedirs(self.output_dir, exist_ok=True)
        os.makedirs(self.downloads_dir, exist_ok=True)

        db.init_db(self.db_path)

        app_module.app.config["TESTING"] = True
        app_module.app.config["SECRET_KEY"] = "test-key"
        self.client = app_module.app.test_client()

        self._orig_db_path = config.DB_PATH
        self._orig_out_dir = config.OUTPUT_DIR
        self._orig_ytdlp_dir = config.YTDLP_OUTDIR
        config.DB_PATH = self.db_path
        config.OUTPUT_DIR = self.output_dir
        config.YTDLP_OUTDIR = self.downloads_dir

    def tearDown(self):
        config.DB_PATH = self._orig_db_path
        config.OUTPUT_DIR = self._orig_out_dir
        config.YTDLP_OUTDIR = self._orig_ytdlp_dir
        os.makedirs(os.path.dirname(self._orig_db_path), exist_ok=True)
        db.init_db(self._orig_db_path)
        import shutil
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_caption_presets_metadata_covers_all_presets(self):
        for preset_name in CAPTION_PRESETS:
            self.assertIn(preset_name, CAPTION_PRESETS_METADATA)
            meta = CAPTION_PRESETS_METADATA[preset_name]
            self.assertIn("label", meta)
            self.assertIn("sample", meta)
            self.assertIn("css", meta)
            self.assertIn("description", meta)

    def test_index_renders_caption_presets_selector(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn("Estilo da legenda".encode(), response.data)
        self.assertIn(b"Karaoke Destaque", response.data)
        self.assertIn(b"progressive-word-highlight", response.data)

    def test_render_kwargs_for_template_applies_caption_preset_override(self):
        kwargs = app_module._render_kwargs_for_template(None, caption_preset_override="yellow-bold")
        self.assertEqual(kwargs.get("caption_preset"), "yellow-bold")


if __name__ == "__main__":
    unittest.main()
