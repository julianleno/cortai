import os
import tempfile
import unittest
from cortai.core import add_watermark, create_default_watermark
import ffmpeg

class TestWatermarkFunctions(unittest.TestCase):
    
    def setUp(self):
        # Create a temporary directory for test files
        self.test_dir = tempfile.mkdtemp()
        self.output_dir = os.path.join(self.test_dir, "output")
        os.makedirs(self.output_dir, exist_ok=True)
        
        # Create a small test video
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
    
    def test_create_default_watermark(self):
        """Test creating a default watermark"""
        watermark_path = create_default_watermark()
        
        # Check that the watermark file exists
        self.assertTrue(os.path.exists(watermark_path))
        
        # Check that it's a PNG file
        self.assertTrue(watermark_path.endswith('.png'))
        
        # Check that we can probe it as an image
        probe = ffmpeg.probe(watermark_path)
        self.assertIn('streams', probe)
        # Should have at least one video stream
        video_streams = [stream for stream in probe['streams'] if stream['codec_type'] == 'video']
        self.assertGreater(len(video_streams), 0)
    
    def test_add_watermark(self):
        """Test adding a watermark to a video"""
        # Create a watermark
        watermark_path = create_default_watermark()
        
        # Apply watermark to video
        output_path = os.path.join(self.output_dir, "watermarked_video.mp4")
        result = add_watermark(self.test_video, watermark_path, output_path)
        
        # Check that the output file exists
        self.assertTrue(os.path.exists(result))
        self.assertEqual(result, output_path)
        
        # Check that the video can be probed
        probe = ffmpeg.probe(result)
        self.assertIn('streams', probe)
        
        # Check that duration is preserved
        original_probe = ffmpeg.probe(self.test_video)
        original_duration = float(next(stream for stream in original_probe['streams'] if stream['codec_type'] == 'video')['duration'])
        
        watermarked_duration = float(next(stream for stream in probe['streams'] if stream['codec_type'] == 'video')['duration'])
        self.assertAlmostEqual(original_duration, watermarked_duration, places=1)

if __name__ == '__main__':
    unittest.main()
