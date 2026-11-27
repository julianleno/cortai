import os
import tempfile
import unittest
from unittest import mock

import ffmpeg

from cortai import core as core_module
from cortai.core import render_clip_from_source, transcribe_audio, extract_audio
from cortai.segmentation import flatten_words


class TestRenderClipFromSource(unittest.TestCase):
    """Fase 7: renders a specific clip out of an already-downloaded video and
    an already-computed full-video transcription, without re-downloading or
    re-transcribing. Uses real ffmpeg + real faster-whisper end to end."""

    @classmethod
    def setUpClass(cls):
        cls.test_dir = tempfile.mkdtemp()

        # Real speech (flite TTS, no network) combined with a real video track.
        audio_path = os.path.join(cls.test_dir, "speech.wav")
        (
            ffmpeg
            .input(
                "flite=text='The first part talks about mountains. The second part talks about rivers instead.':voice=kal16",
                f='lavfi',
            )
            .output(audio_path, ar=16000, ac=1)
            .overwrite_output()
            .run(capture_stdout=True, capture_stderr=True)
        )
        probe = ffmpeg.probe(audio_path)
        cls.duration = float(probe['format']['duration'])

        cls.video_path = os.path.join(cls.test_dir, "source_video.mp4")
        video_in = ffmpeg.input(f"testsrc=duration={cls.duration}:size=640x360:rate=5", f='lavfi')
        audio_in = ffmpeg.input(audio_path)
        (
            ffmpeg
            .output(video_in, audio_in, cls.video_path, vcodec='libx264', acodec='aac', t=cls.duration)
            .overwrite_output()
            .run(capture_stdout=True, capture_stderr=True)
        )

        # Real full-video transcription (this IS the "already computed" transcription
        # that a real job would have cached via transcription.transcribe_video_file).
        transcript_audio = os.path.join(cls.test_dir, "for_transcription.wav")
        extract_audio(cls.video_path, transcript_audio)
        cls.full_transcription = transcribe_audio(transcript_audio, language="en")

    @classmethod
    def tearDownClass(cls):
        import shutil
        shutil.rmtree(cls.test_dir, ignore_errors=True)

    def test_renders_correct_segment_at_target_resolution(self):
        output_dir = os.path.join(self.test_dir, "output1")
        result = render_clip_from_source(
            self.video_path, self.full_transcription, start=0.0, end=self.duration,
            output_dir=output_dir, output_filename="clip.mp4",
            target_width=200, target_height=356, caption_preset="white-bold",
        )
        self.assertTrue(os.path.exists(result))
        probe = ffmpeg.probe(result)
        video_info = next(s for s in probe['streams'] if s['codec_type'] == 'video')
        self.assertEqual(int(video_info['width']), 200)
        self.assertEqual(int(video_info['height']), 356)
        # Regression: crop_to_vertical used to silently drop audio (only
        # caught by testing against a real video with real narration).
        self.assertTrue(any(s['codec_type'] == 'audio' for s in probe['streams']),
                        "final rendered clip must keep the audio track")

    def test_rerender_with_different_template_produces_different_output(self):
        """Directly exercises the Fase 7 acceptance criterion: re-render the
        same suggested clip with a different template, without re-downloading
        or re-transcribing (both video_path and full_transcription are reused
        as-is across both calls)."""
        output_dir = os.path.join(self.test_dir, "output2")

        result_a = render_clip_from_source(
            self.video_path, self.full_transcription, start=0.0, end=self.duration,
            output_dir=output_dir, output_filename="clip_a.mp4",
            target_width=200, target_height=356, caption_preset="white-bold", watermark_path="",
        )
        result_b = render_clip_from_source(
            self.video_path, self.full_transcription, start=0.0, end=self.duration,
            output_dir=output_dir, output_filename="clip_b.mp4",
            target_width=200, target_height=356, caption_preset="cyan-shadow", watermark_path="",
        )

        with open(result_a, "rb") as f:
            bytes_a = f.read()
        with open(result_b, "rb") as f:
            bytes_b = f.read()
        self.assertNotEqual(bytes_a, bytes_b)

    def test_only_words_within_range_are_used_for_captions(self):
        """Renders just the second half of the clip and confirms the ASS
        captions generated internally only reference the later words (proven
        indirectly via slice_transcription, already unit-tested; here we
        confirm the full pipeline doesn't error and produces a shorter clip)."""
        midpoint = self.duration / 2
        output_dir = os.path.join(self.test_dir, "output3")
        result = render_clip_from_source(
            self.video_path, self.full_transcription, start=midpoint, end=self.duration,
            output_dir=output_dir, output_filename="second_half.mp4",
            target_width=200, target_height=356, watermark_path="",
        )
        probe = ffmpeg.probe(result)
        video_info = next(s for s in probe['streams'] if s['codec_type'] == 'video')
        rendered_duration = float(video_info['duration'])
        self.assertLess(rendered_duration, self.duration)


    def test_renders_with_blurred_background_layout(self):
        """Fase 9: layout='blurred_background' must flow all the way through
        render_clip_from_source -> render_vertical_clip and produce a real,
        playable vertical clip (with captions and audio) -- not just exercise
        crop_to_vertical_blurred_background in isolation."""
        output_dir = os.path.join(self.test_dir, "output_blurred")
        result = render_clip_from_source(
            self.video_path, self.full_transcription, start=0.0, end=self.duration,
            output_dir=output_dir, output_filename="blurred_layout.mp4",
            target_width=200, target_height=356, layout="blurred_background", watermark_path="",
        )
        self.assertTrue(os.path.exists(result))
        probe = ffmpeg.probe(result)
        video_info = next(s for s in probe['streams'] if s['codec_type'] == 'video')
        self.assertEqual(int(video_info['width']), 200)
        self.assertEqual(int(video_info['height']), 356)
        self.assertTrue(any(s['codec_type'] == 'audio' for s in probe['streams']))

    def test_vocabulary_and_word_overrides_are_applied_before_rendering(self):
        """Fase 9: proves render_clip_from_source threads vocabulary
        auto-correction and manual word_overrides through to the transcription
        that actually reaches captioning -- the correction logic itself is
        unit-tested in test_vocabulary.py; this confirms the wiring."""
        words = flatten_words(self.full_transcription)
        self.assertGreater(len(words), 3)
        original_word_text = words[2]["word"]

        captured = {}
        real_render = core_module.render_vertical_clip

        def spy(cut_video, final_path, temp_dir, **kwargs):
            captured['transcription'] = kwargs.get('transcription')
            return real_render(cut_video, final_path, temp_dir, **kwargs)

        output_dir = os.path.join(self.test_dir, "output_vocab")

        with mock.patch.object(core_module, 'render_vertical_clip', side_effect=spy):
            render_clip_from_source(
                self.video_path, self.full_transcription, start=0.0, end=self.duration,
                output_dir=output_dir, output_filename="vocab_test.mp4",
                target_width=200, target_height=356, watermark_path="",
                word_overrides={2: "CORRIGIDO"},
            )

        corrected_words = flatten_words(captured['transcription'])
        self.assertIn("CORRIGIDO", corrected_words[2]["word"])
        self.assertNotEqual(corrected_words[2]["word"].strip(), original_word_text.strip())


if __name__ == "__main__":
    unittest.main()
