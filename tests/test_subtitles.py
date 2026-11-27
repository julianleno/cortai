import json
import os
import tempfile
import unittest

import ffmpeg

from cortai.subtitles import (
    CAPTION_PRESETS,
    build_ass_subtitle,
    chunk_words_for_captions,
    format_ass_timestamp,
    validate_ass_content,
    wrap_chunk_into_word_groups,
)

FIXTURE_PATH = os.path.join(os.path.dirname(__file__), "fixtures", "flite_sample_transcript.json")


def _load_real_fixture() -> dict:
    with open(FIXTURE_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def _make_words(specs):
    """specs: list of (word, start, end)"""
    return [{"word": w, "start": s, "end": e, "probability": 0.99} for w, s, e in specs]


class TestChunking(unittest.TestCase):

    def test_breaks_on_max_words(self):
        words = _make_words([(f"w{i}", i * 0.3, i * 0.3 + 0.2) for i in range(10)])
        chunks = chunk_words_for_captions(words, max_words=4, max_duration=100, pause_threshold=100)
        self.assertTrue(all(len(c) <= 4 for c in chunks))
        self.assertEqual(sum(len(c) for c in chunks), 10)

    def test_breaks_on_max_duration(self):
        words = _make_words([(f"w{i}", i * 1.0, i * 1.0 + 0.5) for i in range(10)])
        chunks = chunk_words_for_captions(words, max_words=100, max_duration=2.0, pause_threshold=100)
        for chunk in chunks:
            self.assertLessEqual(chunk[-1]["end"] - chunk[0]["start"], 2.0 + 1e-9)

    def test_breaks_on_natural_pause(self):
        words = _make_words([
            ("Hello", 0.0, 0.3), ("there", 0.3, 0.6),
            ("Big", 2.0, 2.3), ("pause", 2.3, 2.6),
        ])
        chunks = chunk_words_for_captions(words, max_words=100, max_duration=100, pause_threshold=0.5)
        self.assertEqual(len(chunks), 2)
        self.assertEqual([w["word"] for w in chunks[0]], ["Hello", "there"])


class TestLineWrapping(unittest.TestCase):

    def test_short_chunk_stays_one_line(self):
        words = _make_words([("a", 0, 1), ("b", 1, 2), ("c", 2, 3)])
        groups = wrap_chunk_into_word_groups(words)
        self.assertEqual(len(groups), 1)

    def test_long_chunk_splits_into_exactly_two_lines(self):
        words = _make_words([(f"w{i}", i, i + 1) for i in range(6)])
        groups = wrap_chunk_into_word_groups(words)
        self.assertEqual(len(groups), 2)
        self.assertEqual(sum(len(g) for g in groups), 6)

    def test_never_more_than_two_lines(self):
        words = _make_words([(f"w{i}", i, i + 1) for i in range(20)])
        groups = wrap_chunk_into_word_groups(words)
        self.assertLessEqual(len(groups), 2)


class TestAssTimestampFormat(unittest.TestCase):

    def test_zero(self):
        self.assertEqual(format_ass_timestamp(0.0), "0:00:00.00")

    def test_rounds_centiseconds_without_overflow(self):
        # 1.999999s should round to 2.00s, not 1:100 (invalid).
        self.assertEqual(format_ass_timestamp(1.999999), "0:00:02.00")

    def test_handles_hours(self):
        self.assertEqual(format_ass_timestamp(3661.5), "1:01:01.50")

    def test_negative_clamped_to_zero(self):
        self.assertEqual(format_ass_timestamp(-1.0), "0:00:00.00")


class TestValidateAssContent(unittest.TestCase):

    def test_valid_content_passes(self):
        content = (
            "[Script Info]\n\n[Events]\n"
            "Dialogue: 0,0:00:00.00,0:00:01.00,Default,,0,0,0,,Hello world\n"
        )
        validate_ass_content(content)  # should not raise

    def test_vtt_style_tag_raises(self):
        content = "Dialogue: 0,0:00:00.00,0:00:01.00,Default,,0,0,0,,<b>Hello</b>"
        with self.assertRaises(ValueError):
            validate_ass_content(content)

    def test_more_than_two_lines_raises(self):
        content = "Dialogue: 0,0:00:00.00,0:00:01.00,Default,,0,0,0,,linha1\\Nlinha2\\Nlinha3"
        with self.assertRaises(ValueError):
            validate_ass_content(content)


class TestBuildAssSubtitleAllPresets(unittest.TestCase):

    def setUp(self):
        self.transcription = _load_real_fixture()
        self.test_dir = tempfile.mkdtemp()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.test_dir)

    def test_every_preset_produces_valid_ass_file(self):
        for preset_name in CAPTION_PRESETS:
            output_path = os.path.join(self.test_dir, f"{preset_name}.ass")
            result = build_ass_subtitle(self.transcription, preset_name, 1080, 1920, output_path)
            self.assertTrue(os.path.exists(result))
            with open(result, "r", encoding="utf-8") as f:
                content = f.read()
            self.assertIn("[Script Info]", content)
            self.assertIn("[Events]", content)
            self.assertIn("Dialogue:", content)
            self.assertNotRegex(content, r"<[^>]+>")

    def test_karaoke_preset_emits_kf_tags(self):
        output_path = os.path.join(self.test_dir, "karaoke.ass")
        build_ass_subtitle(self.transcription, "progressive-word-highlight", 1080, 1920, output_path)
        with open(output_path, "r", encoding="utf-8") as f:
            content = f.read()
        self.assertIn("\\kf", content)

    def test_keyword_preset_emits_color_override(self):
        output_path = os.path.join(self.test_dir, "keyword.ass")
        build_ass_subtitle(self.transcription, "white-yellow-keyword", 1080, 1920, output_path)
        with open(output_path, "r", encoding="utf-8") as f:
            content = f.read()
        self.assertIn("\\c&H00FFFF&", content)

    def test_uppercase_preserves_portuguese_accents(self):
        transcription = {
            "segments": [{
                "id": 0, "start": 0.0, "end": 2.0, "text": "nao e a acao",
                "words": [
                    {"word": " Não", "start": 0.0, "end": 0.4, "probability": 0.99},
                    {"word": " é", "start": 0.4, "end": 0.6, "probability": 0.99},
                    {"word": " ação", "start": 0.6, "end": 1.0, "probability": 0.99},
                ],
            }],
        }
        output_path = os.path.join(self.test_dir, "accents.ass")
        build_ass_subtitle(transcription, "white-bold", 1080, 1920, output_path)  # uppercase preset
        with open(output_path, "r", encoding="utf-8") as f:
            content = f.read()
        self.assertIn("NÃO", content)
        self.assertIn("É", content)
        self.assertIn("AÇÃO", content)

    def test_unknown_preset_raises(self):
        with self.assertRaises(ValueError):
            build_ass_subtitle(self.transcription, "does-not-exist", 1080, 1920,
                                os.path.join(self.test_dir, "x.ass"))

    def test_empty_transcription_raises(self):
        with self.assertRaises(ValueError):
            build_ass_subtitle({"segments": []}, "white-bold", 1080, 1920,
                                os.path.join(self.test_dir, "x.ass"))


class TestStyledSubtitleRealBurnIn(unittest.TestCase):
    """End-to-end (real ffmpeg + real libass, no mocking): burns each preset
    into an actual video and confirms the output is a valid, playable file."""

    def setUp(self):
        self.transcription = _load_real_fixture()
        self.test_dir = tempfile.mkdtemp()
        self.input_video = os.path.join(self.test_dir, "input.mp4")
        (
            ffmpeg
            .input('testsrc=duration=6:size=1080x1920:rate=2', f='lavfi')
            .output(self.input_video, vcodec='libx264', t=6)
            .overwrite_output()
            .run(capture_stdout=True, capture_stderr=True)
        )

    def tearDown(self):
        import shutil
        shutil.rmtree(self.test_dir)

    def test_each_preset_burns_into_a_real_playable_video(self):
        from cortai.core import add_styled_subtitles

        for preset_name in CAPTION_PRESETS:
            ass_path = os.path.join(self.test_dir, f"{preset_name}.ass")
            build_ass_subtitle(self.transcription, preset_name, 1080, 1920, ass_path)

            output_path = os.path.join(self.test_dir, f"{preset_name}.mp4")
            result = add_styled_subtitles(self.input_video, ass_path, output_path)

            self.assertTrue(os.path.exists(result))
            probe = ffmpeg.probe(result)
            video_info = next(s for s in probe['streams'] if s['codec_type'] == 'video')
            self.assertEqual(int(video_info['width']), 1080)
            self.assertEqual(int(video_info['height']), 1920)


if __name__ == "__main__":
    unittest.main()
