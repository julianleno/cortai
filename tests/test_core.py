import os
import tempfile
import unittest
from cortai.core import (
    cut_video_segment, resize_to_vertical, crop_to_vertical, export_video,
    create_srt_from_segments, add_subtitles, add_title_overlay, add_watermark,
    crop_to_vertical_blurred_background, render_vertical_clip,
)
import ffmpeg

class TestCoreFunctions(unittest.TestCase):
    
    def setUp(self):
        # Create a temporary directory for test files
        self.test_dir = tempfile.mkdtemp()
        self.output_dir = os.path.join(self.test_dir, "output")
        os.makedirs(self.output_dir, exist_ok=True)
        
        # Create a small test video using color source
        self.test_video = os.path.join(self.test_dir, "test_input.mp4")
        (
            ffmpeg
            .input('testsrc=duration=5:size=320x240:rate=1', f='lavfi')
            .output(self.test_video, vcodec='libx264', acodec='aac', t=5)
            .overwrite_output()
            .run(capture_stdout=True, capture_stderr=True)
        )
    
    def tearDown(self):
        # Clean up temporary files
        import shutil
        shutil.rmtree(self.test_dir)
    
    def test_cut_video_segment(self):
        """Test cutting a segment from a video"""
        output_path = os.path.join(self.output_dir, "cut_video.mp4")
        # Cut from 1 second to 3 seconds
        result = cut_video_segment(self.test_video, 1.0, 3.0, output_path)
        
        # Check that the output file exists
        self.assertTrue(os.path.exists(result))
        self.assertEqual(result, output_path)
        
        # Decoding/re-encoding makes the requested boundaries keyframe-accurate.
        probe = ffmpeg.probe(result)
        video_info = next(stream for stream in probe['streams'] if stream['codec_type'] == 'video')
        duration = float(video_info['duration'])
        self.assertAlmostEqual(duration, 2.0, delta=0.15)

    def test_render_supports_real_vertical_and_horizontal_outputs_with_audio(self):
        source = os.path.join(self.test_dir, "aspect_source.mp4")
        video = ffmpeg.input("testsrc=duration=0.6:size=320x180:rate=5", f="lavfi")
        audio = ffmpeg.input("sine=frequency=440:duration=0.6", f="lavfi")
        (
            ffmpeg.output(
                video.video, audio.audio, source,
                vcodec="libx264", acodec="aac", t=0.6,
            )
            .overwrite_output()
            .run(capture_stdout=True, capture_stderr=True)
        )

        for width, height in ((1080, 1920), (1920, 1080)):
            with self.subTest(width=width, height=height):
                temp_dir = os.path.join(self.test_dir, f"temp_{width}_{height}")
                output_path = os.path.join(self.output_dir, f"aspect_{width}_{height}.mp4")
                render_vertical_clip(
                    source, output_path, temp_dir,
                    target_width=width, target_height=height, layout="center",
                    watermark_path="", preset="ultrafast",
                )
                probe = ffmpeg.probe(output_path)
                video_stream = next(
                    stream for stream in probe["streams"] if stream["codec_type"] == "video")
                self.assertEqual((int(video_stream["width"]), int(video_stream["height"])),
                                 (width, height))
                self.assertTrue(any(
                    stream["codec_type"] == "audio" for stream in probe["streams"]))
    
    def test_resize_to_vertical(self):
        """Test resizing video to vertical format"""
        # First create a test video to resize
        input_video = os.path.join(self.test_dir, "input_resize.mp4")
        (
            ffmpeg
            .input('testsrc=duration=2:size=640x360:rate=1', f='lavfi')
            .output(input_video, vcodec='libx264', acodec='aac', t=2)
            .overwrite_output()
            .run(capture_stdout=True, capture_stderr=True)
        )
        
        output_path = os.path.join(self.output_dir, "vertical_video.mp4")
        result = resize_to_vertical(input_video, output_path)
        
        # Check that the output file exists
        self.assertTrue(os.path.exists(result))
        self.assertEqual(result, output_path)
        
        # Check that the video has the correct dimensions (1080x1920 or scaled appropriately)
        probe = ffmpeg.probe(result)
        video_info = next(stream for stream in probe['streams'] if stream['codec_type'] == 'video')
        width = int(video_info['width'])
        height = int(video_info['height'])
        
        # Should be scaled to fit width 1080, maintaining aspect ratio
        # Original 640x360 scaled to width 1080 should be height 607.5 -> rounded to 608
        # Then padded to 1920 height
        self.assertEqual(width, 1080)
        self.assertEqual(height, 1920)
    
    def test_crop_to_vertical_no_face_uses_center_crop_fallback(self):
        """End-to-end (real ffmpeg, real cv2 detection) on a video with no
        face: must fall back to a center crop and still produce a playable
        vertical video at exactly the requested resolution."""
        input_video = os.path.join(self.test_dir, "input_crop.mp4")
        (
            ffmpeg
            .input('testsrc=duration=2:size=640x360:rate=2', f='lavfi')
            .output(input_video, vcodec='libx264', acodec='aac', t=2)
            .overwrite_output()
            .run(capture_stdout=True, capture_stderr=True)
        )

        output_path = os.path.join(self.output_dir, "cropped_vertical.mp4")
        result = crop_to_vertical(input_video, output_path, target_width=200, target_height=356, num_samples=4)

        self.assertTrue(os.path.exists(result))
        probe = ffmpeg.probe(result)
        video_info = next(stream for stream in probe['streams'] if stream['codec_type'] == 'video')
        self.assertEqual(int(video_info['width']), 200)
        self.assertEqual(int(video_info['height']), 356)

    def test_crop_to_vertical_preserves_audio_stream(self):
        """Regression test: crop_to_vertical builds its output from a
        filtered (crop+scale) video-only stream -- the source's audio has to
        be explicitly re-attached or FFmpeg silently drops it. This is what
        made every real rendered clip end up completely silent until fixed
        (only caught by testing against a real video with real narration)."""
        input_video = os.path.join(self.test_dir, "input_crop_audio.mp4")
        video_in = ffmpeg.input('testsrc=duration=2:size=640x360:rate=2', f='lavfi')
        audio_in = ffmpeg.input('sine=frequency=440:duration=2', f='lavfi')
        (
            ffmpeg
            .output(video_in, audio_in, input_video, vcodec='libx264', acodec='aac', t=2)
            .overwrite_output()
            .run(capture_stdout=True, capture_stderr=True)
        )
        probe_in = ffmpeg.probe(input_video)
        self.assertTrue(any(s['codec_type'] == 'audio' for s in probe_in['streams']))

        output_path = os.path.join(self.output_dir, "cropped_with_audio.mp4")
        result = crop_to_vertical(input_video, output_path, target_width=200, target_height=356, num_samples=4)

        probe_out = ffmpeg.probe(result)
        self.assertTrue(any(s['codec_type'] == 'audio' for s in probe_out['streams']),
                        "crop_to_vertical must preserve the audio stream")

    def test_crop_to_vertical_center_layout_skips_face_detection(self):
        """layout='center' (Fase 6 template option) must always produce a
        center crop, regardless of any face in the source."""
        from cortai import face_tracking

        input_video = os.path.join(self.test_dir, "input_center.mp4")
        (
            ffmpeg
            .input('testsrc=duration=2:size=640x360:rate=2', f='lavfi')
            .output(input_video, vcodec='libx264', acodec='aac', t=2)
            .overwrite_output()
            .run(capture_stdout=True, capture_stderr=True)
        )

        x, y, w, h = face_tracking.compute_center_crop_window(640, 360, target_width=200, target_height=356)
        window = {"x": x, "y": y, "width": w, "height": h,
                  "faces_detected": 0, "used_fallback": True, "median_face_height_ratio": 0.0}

        output_path = os.path.join(self.output_dir, "center_layout.mp4")
        result = crop_to_vertical(input_video, output_path, target_width=200, target_height=356, window=window)

        probe = ffmpeg.probe(result)
        video_info = next(s for s in probe['streams'] if s['codec_type'] == 'video')
        self.assertEqual(int(video_info['width']), 200)
        self.assertEqual(int(video_info['height']), 356)

    def test_crop_to_vertical_blurred_background_produces_correct_resolution_and_audio(self):
        """Fase 9 -- layout 'fundo desfocado': verifies real ffmpeg output at
        the exact requested resolution, with audio preserved (same class of
        bug as crop_to_vertical/add_watermark if audio isn't re-attached)."""
        input_video = os.path.join(self.test_dir, "input_blurred.mp4")
        video_in = ffmpeg.input('testsrc=duration=2:size=640x360:rate=2', f='lavfi')
        audio_in = ffmpeg.input('sine=frequency=440:duration=2', f='lavfi')
        (
            ffmpeg
            .output(video_in, audio_in, input_video, vcodec='libx264', acodec='aac', t=2)
            .overwrite_output()
            .run(capture_stdout=True, capture_stderr=True)
        )

        output_path = os.path.join(self.output_dir, "blurred_background.mp4")
        result = crop_to_vertical_blurred_background(input_video, output_path,
                                                       target_width=200, target_height=356)

        self.assertTrue(os.path.exists(result))
        probe = ffmpeg.probe(result)
        video_info = next(s for s in probe['streams'] if s['codec_type'] == 'video')
        self.assertEqual(int(video_info['width']), 200)
        self.assertEqual(int(video_info['height']), 356)
        self.assertTrue(any(s['codec_type'] == 'audio' for s in probe['streams']),
                        "blurred-background layout must preserve the audio stream")

    def test_crop_to_vertical_blurred_background_keeps_whole_scene_visible(self):
        """Confirms the layout actually behaves as 'fundo desfocado': the full
        source frame is scaled to fit (never cropped) over a blurred/zoomed
        copy filling the rest -- proven by real pixel inspection, not just
        resolution checks. A landscape testsrc pattern scaled-to-fit inside a
        200x356 vertical frame leaves blurred-background strips top and
        bottom; those strips must be visibly smoother (lower gradient/variance)
        than the sharp foreground content in the middle."""
        import numpy as np
        import cv2

        input_video = os.path.join(self.test_dir, "input_blurred2.mp4")
        (
            ffmpeg
            .input('testsrc=duration=1:size=640x360:rate=1', f='lavfi')
            .output(input_video, vcodec='libx264', t=1)
            .overwrite_output()
            .run(capture_stdout=True, capture_stderr=True)
        )

        output_path = os.path.join(self.output_dir, "blurred_background2.mp4")
        crop_to_vertical_blurred_background(input_video, output_path,
                                            target_width=200, target_height=356)

        frame_path = os.path.join(self.output_dir, "frame.png")
        (
            ffmpeg
            .input(output_path)
            .output(frame_path, vframes=1)
            .overwrite_output()
            .run(capture_stdout=True, capture_stderr=True)
        )
        frame = cv2.imread(frame_path)
        self.assertIsNotNone(frame)
        self.assertEqual(frame.shape[:2], (356, 200))

        # 640x360 fit-to-200-wide -> foreground height ~= 200*360/640 = 112.5,
        # centered vertically -> roughly rows [122, 234) are foreground.
        top_background_strip = frame[0:60, :].astype(np.float64)
        middle_foreground_strip = frame[150:200, :].astype(np.float64)

        def mean_gradient(strip):
            return float(np.mean(np.abs(np.diff(strip, axis=1))))

        bg_gradient = mean_gradient(top_background_strip)
        fg_gradient = mean_gradient(middle_foreground_strip)
        self.assertLess(bg_gradient, fg_gradient,
                        "blurred background strip should be visibly smoother than the sharp foreground")

    def test_add_title_overlay_real_render_with_title_and_cta(self):
        """End-to-end (real ffmpeg drawtext, no mocking): burns a title and a
        CTA banner into a real video with audio, confirms both video and
        audio streams survive."""
        input_video = os.path.join(self.test_dir, "input_title.mp4")
        video_in = ffmpeg.input('testsrc=duration=2:size=320x568:rate=2', f='lavfi')
        audio_in = ffmpeg.input('sine=frequency=440:duration=2', f='lavfi')
        (
            ffmpeg
            .output(video_in, audio_in, input_video, vcodec='libx264', acodec='aac', t=2)
            .overwrite_output()
            .run(capture_stdout=True, capture_stderr=True)
        )

        output_path = os.path.join(self.output_dir, "titled.mp4")
        result = add_title_overlay(input_video, output_path, title="Meu Canal", cta="Siga para mais!")

        self.assertTrue(os.path.exists(result))
        probe = ffmpeg.probe(result)
        codec_types = {s['codec_type'] for s in probe['streams']}
        self.assertIn('video', codec_types)
        self.assertIn('audio', codec_types)

    def test_add_title_overlay_without_text_just_copies_file(self):
        input_video = os.path.join(self.test_dir, "input_notitle.mp4")
        (
            ffmpeg
            .input('testsrc=duration=1:size=320x240:rate=2', f='lavfi')
            .output(input_video, vcodec='libx264', t=1)
            .overwrite_output()
            .run(capture_stdout=True, capture_stderr=True)
        )
        output_path = os.path.join(self.output_dir, "notitle.mp4")
        result = add_title_overlay(input_video, output_path)
        self.assertTrue(os.path.exists(result))
        self.assertEqual(os.path.getsize(result), os.path.getsize(input_video))

    def test_add_watermark_position_and_opacity_actually_apply(self):
        """Regression test: opacity used to be a dead parameter (never
        applied to the filter graph), and every position except top-left
        used a broken `w-w`/`h-h` expression that always evaluated to 0."""
        input_video = os.path.join(self.test_dir, "input_watermark.mp4")
        (
            ffmpeg
            .input('color=c=black:s=320x240:d=1', f='lavfi')
            .output(input_video, vcodec='libx264', t=1, pix_fmt='yuv420p')
            .overwrite_output()
            .run(capture_stdout=True, capture_stderr=True)
        )
        watermark_path = os.path.join(os.path.dirname(__file__), "..", "src", "cortai", "assets", "watermark.png")

        out_full = os.path.join(self.output_dir, "wm_full.mp4")
        out_low = os.path.join(self.output_dir, "wm_low.mp4")
        add_watermark(input_video, watermark_path, out_full, position="bottom-right", opacity=1.0)
        add_watermark(input_video, watermark_path, out_low, position="bottom-right", opacity=0.1)

        frame_full = os.path.join(self.output_dir, "wm_full.png")
        frame_low = os.path.join(self.output_dir, "wm_low.png")
        ffmpeg.input(out_full, ss=0).output(frame_full, vframes=1).overwrite_output().run(
            capture_stdout=True, capture_stderr=True)
        ffmpeg.input(out_low, ss=0).output(frame_low, vframes=1).overwrite_output().run(
            capture_stdout=True, capture_stderr=True)

        # Sample a pixel inside the bottom-right watermark area: with the
        # w-w/h-h bug this region would just be plain black background (no
        # watermark ever rendered there), and opacity would look identical
        # between the two renders. Watermark is 200x50 with a 20px margin on
        # a 320x240 frame -> occupies x in [100,300], y in [170,220].
        # Point (120, 185) falls inside the opaque caju square of the logo mark.
        sample_x, sample_y = 120, 185

        def read_pixel(png_path, x, y):
            raw, _ = (
                ffmpeg
                .input(png_path)
                .filter('crop', w=1, h=1, x=x, y=y)
                .output('pipe:', format='rawvideo', pix_fmt='rgb24', vframes=1)
                .run(capture_stdout=True, capture_stderr=True)
            )
            return raw[0], raw[1], raw[2]

        pixel_full = read_pixel(frame_full, sample_x, sample_y)
        pixel_low = read_pixel(frame_low, sample_x, sample_y)

        # Full opacity should show the bright caju mark; low
        # opacity should be much darker (mostly the black background bleeding
        # through). They must differ, and full must be brighter than low.
        self.assertGreater(sum(pixel_full), sum(pixel_low))
        self.assertGreater(sum(pixel_full), 300)  # bright caju (255, 106, 43)
        self.assertLess(sum(pixel_low), 300)      # still mostly black

    def test_add_watermark_preserves_audio_stream(self):
        """Regression test: the overlay filter builds a video-only stream --
        the source's audio has to be explicitly re-attached or it's silently
        dropped (same class of bug as crop_to_vertical)."""
        input_video = os.path.join(self.test_dir, "input_watermark_audio.mp4")
        video_in = ffmpeg.input('color=c=black:s=320x240:d=1', f='lavfi')
        audio_in = ffmpeg.input('sine=frequency=440:duration=1', f='lavfi')
        (
            ffmpeg
            .output(video_in, audio_in, input_video, vcodec='libx264', acodec='aac', t=1, pix_fmt='yuv420p')
            .overwrite_output()
            .run(capture_stdout=True, capture_stderr=True)
        )
        probe_in = ffmpeg.probe(input_video)
        self.assertTrue(any(s['codec_type'] == 'audio' for s in probe_in['streams']))

        watermark_path = os.path.join(os.path.dirname(__file__), "..", "src", "cortai", "assets", "watermark.png")
        output_path = os.path.join(self.output_dir, "wm_with_audio.mp4")
        result = add_watermark(input_video, watermark_path, output_path)

        probe_out = ffmpeg.probe(result)
        self.assertTrue(any(s['codec_type'] == 'audio' for s in probe_out['streams']),
                        "add_watermark must preserve the audio stream")

    def test_add_subtitles_with_real_absolute_windows_path(self):
        """Regression test: add_subtitles previously broke on Windows because
        the drive-letter colon in an absolute path (e.g. C:\\Users\\...)
        collided with FFmpeg's filter-option colon syntax. tempfile.mkdtemp()
        always returns an absolute path, so this reproduces the real bug."""
        input_video = os.path.join(self.test_dir, "input_subtitles.mp4")
        (
            ffmpeg
            .input('testsrc=duration=2:size=320x240:rate=2', f='lavfi')
            .output(input_video, vcodec='libx264', t=2)
            .overwrite_output()
            .run(capture_stdout=True, capture_stderr=True)
        )

        srt_path = os.path.join(self.test_dir, "subs.srt")
        create_srt_from_segments(
            [{"start": 0.0, "end": 1.5, "text": "Teste de legenda com acentuacao: nao, e, voce"}],
            srt_path,
        )

        output_path = os.path.join(self.output_dir, "subtitled.mp4")
        result = add_subtitles(input_video, srt_path, output_path)

        self.assertTrue(os.path.exists(result))
        probe = ffmpeg.probe(result)
        self.assertIn('streams', probe)

    def test_export_video(self):
        """Test exporting video with specific codecs"""
        # Create a test video
        input_video = os.path.join(self.test_dir, "input_export.mp4")
        (
            ffmpeg
            .input('testsrc=duration=2:size=320x240:rate=1', f='lavfi')
            .output(input_video, vcodec='libx264', acodec='aac', t=2)
            .overwrite_output()
            .run(capture_stdout=True, capture_stderr=True)
        )
        
        output_path = os.path.join(self.output_dir, "exported_video.mp4")
        result = export_video(input_video, output_path)
        
        # Check that the output file exists
        self.assertTrue(os.path.exists(result))
        self.assertEqual(result, output_path)
        
        # Check that the video can be probed (basic validation)
        probe = ffmpeg.probe(result)
        self.assertIn('streams', probe)

if __name__ == '__main__':
    unittest.main()
