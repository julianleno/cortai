import os
import unittest
from cortai.app import app
from cortai import db
from cortai.config import config


class TestCyberpunkAndExe(unittest.TestCase):

    def setUp(self):
        app.config['TESTING'] = True
        app.config['SECRET_KEY'] = 'test-secret'
        self.client = app.test_client()
        db.init_db(config.DB_PATH)

    def test_build_exe_script_exists(self):
        build_script = os.path.join(os.path.dirname(os.path.dirname(__file__)), "build_exe.py")
        self.assertTrue(os.path.exists(build_script))

    def test_base_template_renders_theme_toggle_and_cinema_logo(self):
        response = self.client.get('/')
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'themeToggleBtn', response.data)
        self.assertIn('Cortaí'.encode(), response.data)


if __name__ == "__main__":
    unittest.main()
