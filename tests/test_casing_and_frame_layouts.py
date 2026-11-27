import os
import tempfile
import unittest
import ffmpeg

from cortai.subtitles import format_word_casing, build_ass_subtitle
from cortai.templates import validate_template_config, render_kwargs_from_template, LAYOUTS, CASING_MODES
from cortai.core import crop_to_vertical_card_frame, crop_to_vertical_header_footer_frame


class TestCasingAndFrameLayouts(unittest.TestCase):

    def setUp(self):
        self.test_dir = tempfile.mkdtemp()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_format_word_casing_titlecase_excludes_connectors(self):
        words = ["como", "criar", "um", "canal", "de", "cortes"]
        formatted = [
            format_word_casing(w, casing_mode="titlecase", is_first_word=(i == 0))
            for i, w in enumerate(words)
        ]
        self.assertEqual(" ".join(formatted), "Como Criar um Canal de Cortes")

    def test_format_word_casing_uppercase(self):
        words = ["como", "criar", "um", "canal", "de", "cortes"]
        formatted = [
            format_word_casing(w, casing_mode="uppercase", is_first_word=(i == 0))
            for i, w in enumerate(words)
        ]
        self.assertEqual(" ".join(formatted), "COMO CRIAR UM CANAL DE CORTES")

    def test_silent_video_empty_words_generates_static_subtitle(self):
        empty_transcription = {
            "title": "Vídeo demonstrativo sem áudio",
            "segments": []
        }
        ass_path = os.path.join(self.test_dir, "silent.ass")
        build_ass_subtitle(empty_transcription, "white-bold", 1080, 1920, ass_path, casing_mode="titlecase")

        with open(ass_path, "r", encoding="utf-8") as f:
            content = f.read()

        self.assertIn("Dialogue:", content)
        self.assertIn("Vídeo Demonstrativo sem Áudio", content)

    def test_new_frame_layouts_and_casing_validated_in_templates(self):
        self.assertIn("card_frame", LAYOUTS)
        self.assertIn("header_footer_frame", LAYOUTS)
        self.assertIn("titlecase", CASING_MODES)

        config_input = {
            "layout": "card_frame",
            "casing_mode": "titlecase",
            "caption_preset": "yellow-bold"
        }
        validated = validate_template_config(config_input)
        self.assertEqual(validated["layout"], "card_frame")
        self.assertEqual(validated["casing_mode"], "titlecase")

        kwargs = render_kwargs_from_template(validated)
        self.assertEqual(kwargs.get("casing_mode"), "titlecase")
        self.assertEqual(kwargs.get("layout"), "card_frame")

    def test_frame_layouts_finish_at_source_duration_with_and_without_audio(self):
        renderers = (crop_to_vertical_card_frame, crop_to_vertical_header_footer_frame)
        for has_audio in (False, True):
            source = os.path.join(self.test_dir, f"source_{has_audio}.mp4")
            video = ffmpeg.input("testsrc=duration=1:size=160x90:rate=10", f="lavfi")
            if has_audio:
                audio = ffmpeg.input("sine=frequency=440:duration=1", f="lavfi")
                output = ffmpeg.output(
                    video.video, audio.audio, source,
                    vcodec="libx264", acodec="aac", t=1,
                )
            else:
                output = ffmpeg.output(video.video, source, vcodec="libx264", t=1)
            output.overwrite_output().run(capture_stdout=True, capture_stderr=True)

            for renderer in renderers:
                with self.subTest(has_audio=has_audio, renderer=renderer.__name__):
                    rendered = os.path.join(
                        self.test_dir, f"{renderer.__name__}_{has_audio}.mp4")
                    renderer(source, rendered, target_width=180, target_height=320)
                    probe = ffmpeg.probe(rendered)
                    streams = probe["streams"]
                    self.assertTrue(any(stream["codec_type"] == "video" for stream in streams))
                    self.assertEqual(
                        any(stream["codec_type"] == "audio" for stream in streams), has_audio)
                    self.assertAlmostEqual(float(probe["format"]["duration"]), 1.0, delta=0.15)


if __name__ == "__main__":
    unittest.main()
