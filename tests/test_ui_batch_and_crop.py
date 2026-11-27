import unittest
from unittest import mock
from cortai import app as app_module
from cortai import db
from cortai.app import app
from cortai.config import config


import os
import tempfile

class TestUIBatchAndCrop(unittest.TestCase):

    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self._orig_db = config.DB_PATH
        config.DB_PATH = os.path.join(self.test_dir, "test.db")
        app.config['TESTING'] = True
        app.config['SECRET_KEY'] = 'test-secret'
        self.client = app.test_client()
        db.init_db(config.DB_PATH)

    def tearDown(self):
        import shutil
        config.DB_PATH = self._orig_db
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_projects_route_returns_200(self):
        response = self.client.get('/projects')
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'Meus Projetos', response.data)

    def test_batch_urls_submission_creates_jobs(self):
        data = {
            'urls': 'https://www.youtube.com/watch?v=dQw4w9WgXcQ\nhttps://www.youtube.com/watch?v=jNQXAC9IVRw',
            'aspect_ratio': '9:16',
            'min_duration': '15',
            'max_duration': '60',
            'num_clips': '2'
        }
        with mock.patch.object(app_module.threading.Thread, "start"):
            response = self.client.post('/submit', data=data, follow_redirects=False)
        self.assertEqual(response.status_code, 302)
        self.assertIn('/projects', response.headers['Location'])


if __name__ == '__main__':
    unittest.main()
