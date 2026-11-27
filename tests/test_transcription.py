import os
import tempfile
import unittest
from unittest import mock
from types import SimpleNamespace

from cortai import core, db, transcription
from cortai.config import config


class TestTranscriptionCache(unittest.TestCase):

    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.test_dir, "cortai.db")
        db.init_db(self.db_path)

        self.video_path = os.path.join(self.test_dir, "fake_video.mp4")
        with open(self.video_path, "wb") as f:
            f.write(b"not a real video, only used for its path/size/mtime fingerprint")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.test_dir)

    def test_second_call_uses_cache_and_skips_transcription(self):
        fake_result = {
            "language": "pt",
            "language_probability": 0.99,
            "duration": 12.3,
            "segments": [{"id": 0, "start": 0.0, "end": 1.0, "text": "oi", "words": []}],
            "text": "oi",
        }

        with mock.patch.object(transcription, "extract_audio") as mock_extract, \
             mock.patch.object(transcription, "transcribe_audio", return_value=fake_result) as mock_transcribe:
            first = transcription.transcribe_video_file(self.video_path, self.db_path, language="pt")
            self.assertEqual(mock_extract.call_count, 1)
            self.assertEqual(mock_transcribe.call_count, 1)

            second = transcription.transcribe_video_file(self.video_path, self.db_path, language="pt")
            # Cache hit: neither extraction nor transcription should run again.
            self.assertEqual(mock_extract.call_count, 1)
            self.assertEqual(mock_transcribe.call_count, 1)

        self.assertEqual(first, fake_result)
        self.assertEqual(second, fake_result)

    def test_fingerprint_changes_when_file_content_changes(self):
        fp_before = transcription.compute_video_fingerprint(self.video_path)

        # Simulate the file being re-downloaded/overwritten with different content.
        import time
        time.sleep(0.01)
        with open(self.video_path, "wb") as f:
            f.write(b"different content, different size")
        os.utime(self.video_path, None)

        fp_after = transcription.compute_video_fingerprint(self.video_path)
        self.assertNotEqual(fp_before, fp_after)

    def test_cache_key_includes_language_model_engine_and_cache_version(self):
        with mock.patch.object(transcription, "_faster_whisper_version", return_value="1.0"):
            automatic = transcription.compute_transcription_cache_key(self.video_path, None)
            self.assertEqual(
                automatic,
                transcription.compute_transcription_cache_key(self.video_path, ""),
            )
            self.assertEqual(
                automatic,
                transcription.compute_transcription_cache_key(self.video_path, "auto"),
            )
            self.assertNotEqual(
                automatic,
                transcription.compute_transcription_cache_key(self.video_path, "pt"),
            )

            with mock.patch.object(config, "WHISPER_MODEL_SIZE", "different-model"):
                self.assertNotEqual(
                    automatic,
                    transcription.compute_transcription_cache_key(self.video_path, None),
                )

        with mock.patch.object(transcription, "_faster_whisper_version", return_value="2.0"):
            self.assertNotEqual(
                automatic,
                transcription.compute_transcription_cache_key(self.video_path, None),
            )

        with mock.patch.object(transcription, "TRANSCRIPTION_CACHE_VERSION", "next"):
            self.assertNotEqual(
                automatic,
                transcription.compute_transcription_cache_key(self.video_path, None),
            )


class TestWhisperGeneratorMaterialization(unittest.TestCase):

    def test_transcribe_audio_consumes_generator_once_and_builds_text(self):
        segments = iter([
            SimpleNamespace(start=0.0, end=0.5, text=" Ola", words=[]),
            SimpleNamespace(start=0.5, end=1.0, text=" mundo", words=[]),
        ])
        info = SimpleNamespace(language="pt", language_probability=0.99, duration=1.0)
        model = mock.Mock()
        model.transcribe.return_value = (segments, info)

        with mock.patch.object(core, "WhisperModel", return_value=model):
            result = core.transcribe_audio("unused.wav", language="pt")

        self.assertEqual(len(result["segments"]), 2)
        self.assertEqual(result["text"], "Ola mundo")
        model.transcribe.assert_called_once()


if __name__ == "__main__":
    unittest.main()
