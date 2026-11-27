import os
import sys
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

if getattr(sys, 'frozen', False):
    # Executando via PyInstaller .exe congelado
    APP_DIR = Path(sys.executable).parent
else:
    # Executando em ambiente de desenvolvimento Python
    APP_DIR = Path(os.getcwd())

class Config:
    # YouTube download settings
    YTDLP_FORMAT = os.getenv('YTDLP_FORMAT', 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]')
    YTDLP_OUTDIR = os.path.abspath(os.getenv('YTDLP_OUTDIR', str(APP_DIR / 'storage' / 'downloads')))

    # Transcription settings
    WHISPER_MODEL_SIZE = os.getenv('WHISPER_MODEL_SIZE', 'base')
    WHISPER_DEVICE = os.getenv('WHISPER_DEVICE', 'cpu')
    WHISPER_COMPUTE_TYPE = os.getenv('WHISPER_COMPUTE_TYPE', 'int8')

    # FFmpeg settings
    FFMPEG_THREADS = int(os.getenv('FFMPEG_THREADS', '0'))

    # Web interface
    FLASK_HOST = os.getenv('FLASK_HOST', '127.0.0.1')
    FLASK_PORT = int(os.getenv('FLASK_PORT', '5000'))
    FLASK_DEBUG = os.getenv('FLASK_DEBUG', 'False').lower() == 'true'

    # Output settings
    OUTPUT_DIR = os.path.abspath(os.getenv('OUTPUT_DIR', str(APP_DIR / 'storage' / 'output')))
    TEMP_DIR = os.path.abspath(os.getenv('TEMP_DIR', str(APP_DIR / 'storage' / 'temp')))

    # Persistence
    DB_PATH = os.path.abspath(os.getenv('DB_PATH', str(APP_DIR / 'storage' / 'cortai.db')))

    # AI providers
    GEMINI_API_KEY = os.getenv('GEMINI_API_KEY')
    GEMINI_MODEL = os.getenv('GEMINI_MODEL', 'gemini-flash-latest')
    NVIDIA_NIM_API_KEY = os.getenv('NVIDIA_NIM_API_KEY')
    NVIDIA_NIM_MODEL = os.getenv('NVIDIA_NIM_MODEL', 'meta/llama-3.1-8b-instruct')

    # Watermark
    WATERMARK_IMAGE = os.path.abspath(os.getenv('WATERMARK_IMAGE', str(APP_DIR / 'assets' / 'watermark.png')))

    # Supported languages for transcription
    SUPPORTED_LANGUAGES = ['en', 'pt', 'es', 'fr', 'de', 'it', 'ja', 'ko', 'zh']

config = Config()
