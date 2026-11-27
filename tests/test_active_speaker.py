import os
import tempfile
import unittest
import urllib.request

import ffmpeg

from cortai.core import crop_to_vertical_active_speaker
from cortai.face_tracking import compute_active_speaker_windows

MESSI_IMAGE_URL = "https://raw.githubusercontent.com/opencv/opencv/master/samples/data/messi5.jpg"


class TestActiveSpeakerCropReal(unittest.TestCase):
    """Real, end-to-end proof of active speaker detection (Fase 9): a real
    face image is composited at two clearly different screen positions in
    two time halves of a synthetic video, with real narration (flite) and a
    real transcript. Confirms compute_active_speaker_windows detects the
    face independently in each half (different crop coordinates) and that
    crop_to_vertical_active_speaker renders a valid concatenated output.

    Skipped if network is unavailable (downloading the test face image),
    matching the pattern used for the other live face-detection tests.
    """

    @classmethod
    def setUpClass(cls):
        cls.test_dir = tempfile.mkdtemp()
        cls.image_path = os.path.join(cls.test_dir, "messi5.jpg")
        try:
            urllib.request.urlretrieve(MESSI_IMAGE_URL, cls.image_path)
        except Exception as exc:
            raise unittest.SkipTest(f"Sem acesso de rede para baixar imagem de teste: {exc}")

        # Two flite renders joined with an explicit silence gap: flite's
        # natural inter-sentence pause (~0.3s) landed just under the 0.35s
        # boundary-detection threshold in an earlier version of this test,
        # so the gap is forced here instead of relying on TTS phrasing.
        part1 = os.path.join(cls.test_dir, "part1.wav")
        part2 = os.path.join(cls.test_dir, "part2.wav")
        (
            ffmpeg.input("flite=text='The speaker starts on the left side of the frame.':voice=kal16", f='lavfi')
            .output(part1, ar=16000, ac=1).overwrite_output().run(capture_stdout=True, capture_stderr=True)
        )
        (
            ffmpeg.input("flite=text='Now a different speaker appears on the right side instead.':voice=kal16", f='lavfi')
            .output(part2, ar=16000, ac=1).overwrite_output().run(capture_stdout=True, capture_stderr=True)
        )
        cls.audio_path = os.path.join(cls.test_dir, "speech.wav")
        silence = ffmpeg.input("anullsrc=r=16000:cl=mono", f='lavfi')
        joined_audio = ffmpeg.concat(
            ffmpeg.input(part1).audio, silence.audio.filter('atrim', duration=0.6),
            ffmpeg.input(part2).audio, v=0, a=1,
        )
        joined_audio.output(cls.audio_path, ar=16000, ac=1).overwrite_output().run(
            capture_stdout=True, capture_stderr=True)
        cls.duration = float(ffmpeg.probe(cls.audio_path)['format']['duration'])
        midpoint = cls.duration / 2

        # Wide 960x450 canvas with the face image scaled to ~300px wide --
        # the original 548x342 image is nearly as wide as a 640px canvas, so
        # "left" and "right" placements barely differed in an earlier
        # version of this test; scaling down further to ~150px made the face
        # too small for YuNet to detect at all (verified: 150px -> 0 samples,
        # 250px+ -> reliable detection at >0.8 confidence). 300px keeps a
        # safe margin above that threshold while still leaving clear
        # separation between the two placements on this wider canvas.
        canvas = ffmpeg.input(f"color=c=0x223344:s=960x450:d={cls.duration}", f='lavfi')
        face_scaled = ffmpeg.input(cls.image_path, loop=1, framerate=5).filter('scale', w=300, h=-1)
        face_split = face_scaled.filter_multi_output('split')
        face_left, face_right = face_split[0], face_split[1]

        step1 = ffmpeg.filter(
            [canvas, face_left], 'overlay', x=20, y='(H-h)/2',
            enable=f'between(t,0,{midpoint})',
        )
        step2 = ffmpeg.filter(
            [step1, face_right], 'overlay', x='W-w-20', y='(H-h)/2',
            enable=f'between(t,{midpoint},{cls.duration})',
        )

        cls.video_path = os.path.join(cls.test_dir, "two_speakers.mp4")
        audio_in = ffmpeg.input(cls.audio_path)
        (
            ffmpeg
            .output(step2, audio_in, cls.video_path, vcodec='libx264', acodec='aac',
                   t=cls.duration, pix_fmt='yuv420p')
            .overwrite_output()
            .run(capture_stdout=True, capture_stderr=True)
        )

        # Real transcription (word-level timestamps) of this real audio.
        from cortai.core import extract_audio, transcribe_audio
        transcript_audio = os.path.join(cls.test_dir, "for_transcription.wav")
        extract_audio(cls.video_path, transcript_audio)
        cls.transcription = transcribe_audio(transcript_audio, language='en')

    @classmethod
    def tearDownClass(cls):
        import shutil
        shutil.rmtree(cls.test_dir, ignore_errors=True)

    def test_windows_detect_face_on_different_sides(self):
        windows = compute_active_speaker_windows(
            self.video_path, self.transcription, target_width=200, target_height=356,
            min_window_duration=1.0, samples_per_window=4,
        )
        self.assertGreaterEqual(len(windows), 2, "expected the transcript's sentence gap to split into >=2 windows")

        first_x = windows[0]["x"]
        last_x = windows[-1]["x"]
        # The face was composited near the left edge (x=0) in the first half
        # and near the right edge (x=W-w) in the second -- the detected crop
        # center must move meaningfully to the right between windows.
        self.assertGreater(last_x, first_x + 100,
                           f"expected crop to shift right between windows, got first={first_x} last={last_x}")

    def test_renders_valid_concatenated_output(self):
        output_path = os.path.join(self.test_dir, "active_speaker_output.mp4")
        result = crop_to_vertical_active_speaker(
            self.video_path, output_path, self.transcription,
            target_width=200, target_height=356, min_window_duration=1.0, samples_per_window=4,
        )
        self.assertTrue(os.path.exists(result))
        probe = ffmpeg.probe(result)
        video_info = next(s for s in probe['streams'] if s['codec_type'] == 'video')
        self.assertEqual(int(video_info['width']), 200)
        self.assertEqual(int(video_info['height']), 356)
        self.assertTrue(any(s['codec_type'] == 'audio' for s in probe['streams']),
                        "concatenated active-speaker output must keep audio")
        self.assertAlmostEqual(float(probe['format']['duration']), self.duration, delta=0.5)


if __name__ == "__main__":
    unittest.main()
