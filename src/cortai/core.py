import os
import subprocess
import json
import tempfile
import logging
import uuid
from pathlib import Path
from typing import Optional, Tuple, List
import yt_dlp
from faster_whisper import WhisperModel
import ffmpeg
import srt
from datetime import timedelta

from .config import config
from .ffmpeg_utils import escape_ffmpeg_filter_path

logger = logging.getLogger(__name__)


def _duration_from_probe(probe: dict) -> float:
    """Return the best positive media duration reported by ffprobe.

    Container duration is normally the most reliable value for MP4/MKV, but
    some inputs only expose duration at stream level. Returning ``0.0`` keeps
    callers explicit about the unknown-duration case instead of inventing a
    value that could desynchronise audio and video.
    """
    candidates = [probe.get('format', {}).get('duration')]
    candidates.extend(stream.get('duration') for stream in probe.get('streams', []))

    durations = []
    for value in candidates:
        try:
            duration = float(value)
        except (TypeError, ValueError):
            continue
        if duration > 0:
            durations.append(duration)
    return max(durations, default=0.0)


def probe_media(video_path: str) -> dict:
    """Inspect media streams once and return rendering/transcription metadata."""
    probe = ffmpeg.probe(video_path)
    streams = probe.get('streams', [])
    return {
        'has_video': any(stream.get('codec_type') == 'video' for stream in streams),
        'has_audio': any(stream.get('codec_type') == 'audio' for stream in streams),
        'duration': _duration_from_probe(probe),
        'probe': probe,
    }

def download_video(url: str, output_dir: str = None) -> str:
    """Download a YouTube video using yt-dlp, with automatic browser cookie fallback
    to bypass 'Sign in to confirm you're not a bot' challenges."""
    if output_dir is None:
        output_dir = config.YTDLP_OUTDIR
    
    os.makedirs(output_dir, exist_ok=True)
    
    base_opts = {
        'format': config.YTDLP_FORMAT,
        'outtmpl': os.path.join(output_dir, '%(title)s.%(ext)s'),
        'restrictfilenames': True,
        'noplaylist': True,
        'enable_file_urls': True,
    }

    try:
        from . import db
        configured_browser = db.get_setting(config.DB_PATH, "ytdlp_cookies_browser", "auto")
    except Exception:
        configured_browser = "auto"

    browser_candidates = []
    if configured_browser and configured_browser != "disabled":
        if configured_browser == "auto":
            browser_candidates = [None, 'chrome', 'edge', 'firefox', 'brave', 'opera']
        else:
            browser_candidates = [configured_browser, None]
    else:
        browser_candidates = [None]

    last_exception = None
    for browser in browser_candidates:
        ydl_opts = dict(base_opts)
        if browser:
            ydl_opts['cookiesfrombrowser'] = (browser,)

        try:
            logger.info("Baixando video %s (browser cookie: %s)", url, browser or "nenhum")
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=True)
                video_path = ydl.prepare_filename(info)
                if not os.path.exists(video_path):
                    base_path = os.path.splitext(video_path)[0]
                    for ext in ['mp4', 'mkv', 'webm']:
                        test_path = f"{base_path}.{ext}"
                        if os.path.exists(test_path):
                            video_path = test_path
                            break
                return video_path
        except Exception as exc:
            logger.warning("Tentativa de download com cookiesfrombrowser=%s falhou: %s", browser, exc)
            last_exception = exc

    raise last_exception

def extract_audio(video_path: str, audio_path: str) -> None:
    """
    Extract audio from video file.
    
    Args:
        video_path: Path to input video
        audio_path: Path to save extracted audio
    """
    try:
        (
            ffmpeg
            .input(video_path)
            .output(audio_path, acodec='pcm_s16le', ac=1, ar='16000')
            .overwrite_output()
            .run(capture_stdout=True, capture_stderr=True)
        )
    except ffmpeg.Error as e:
        logger.error(f"Error extracting audio: {e.stderr.decode()}")
        raise

def transcribe_audio(audio_path: str, language: str = None) -> dict:
    """
    Transcribe audio using faster-whisper.
    
    Args:
        audio_path: Path to audio file
        language: Language code (optional, defaults to auto-detection)
    
    Returns:
        Dictionary with transcription results
    """
    # Load the Whisper model
    model = WhisperModel(
        config.WHISPER_MODEL_SIZE,
        device=config.WHISPER_DEVICE,
        compute_type=config.WHISPER_COMPUTE_TYPE,
        cpu_threads=os.cpu_count() if config.FFMPEG_THREADS == 0 else config.FFMPEG_THREADS,
    )
    
    # Transcribe
    segments, info = model.transcribe(
        audio_path,
        language=language,
        beam_size=5,
        word_timestamps=True,
        vad_filter=True,
        vad_parameters=dict(min_silence_duration_ms=500),
    )
    
    # ``segments`` is a lazy generator in faster-whisper. Consume it exactly
    # once and derive the concatenated text from the materialised payload;
    # iterating it a second time used to produce an empty ``text`` field.
    segment_payloads = []
    for i, segment in enumerate(segments):
        segment_payloads.append({
            "id": i,
            "seek": segment.start,
            "start": segment.start,
            "end": segment.end,
            "text": segment.text,
            "words": [
                {
                    "word": word.word,
                    "start": word.start,
                    "end": word.end,
                    "probability": word.probability,
                }
                for word in segment.words
            ] if segment.words else [],
        })

    return {
        "language": info.language,
        "language_probability": info.language_probability,
        "duration": info.duration,
        "segments": segment_payloads,
        "text": " ".join(
            segment["text"].strip()
            for segment in segment_payloads
            if segment["text"] and segment["text"].strip()
        ),
    }

def cut_video_segment(input_path: str, start_time: float, end_time: float, output_path: str) -> str:
    """
    Cut a segment from a video file.
    
    Args:
        input_path: Path to the input video
        start_time: Start time in seconds
        end_time: End time in seconds
        output_path: Path to save the cut video
    
    Returns:
        Path to the cut video file
    """
    start_time = float(start_time)
    end_time = float(end_time)
    if not (start_time >= 0 and end_time > start_time):
        raise ValueError("start_time deve ser >= 0 e end_time deve ser maior que start_time")

    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    
    try:
        (
            ffmpeg
            .input(input_path, ss=start_time)
            .output(
                output_path,
                t=end_time - start_time,
                vcodec='libx264', acodec='aac', preset='veryfast', crf=18,
                avoid_negative_ts='make_zero', movflags='+faststart',
            )
            .overwrite_output()
            .run(capture_stdout=True, capture_stderr=True)
        )
    except ffmpeg.Error as e:
        logger.error(f"Error cutting video: {e.stderr.decode()}")
        raise
    
    return output_path

def resize_to_vertical(input_path: str, output_path: str, target_width: int = 1080, target_height: int = 1920) -> str:
    """
    Resize video to vertical format (9:16) using crop and pad or blur background.
    
    Args:
        input_path: Path to input video
        output_path: Path to output video
        target_width: Target width (default 1080 for 9:16)
        target_height: Target height (default 1920 for 9:16)
    
    Returns:
        Path to the resized video
    """
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    
    try:
        # Get input video dimensions
        probe = ffmpeg.probe(input_path)
        video_stream = next((stream for stream in probe['streams'] if stream['codec_type'] == 'video'), None)
        if video_stream is None:
            raise ValueError("No video stream found")
        
        width = int(video_stream['width'])
        height = int(video_stream['height'])
        
        # Use a simple approach: scale to fit width, then pad to target height
        vf = (
            f"scale={target_width}:-1,"
            f"pad={target_width}:{target_height}:(ow-iw)/2:(oh-ih)/2"
        )
        
        (
            ffmpeg
            .input(input_path)
            .filter('scale', w=target_width, h=-1)
            .filter('pad', width=target_width, height=target_height, x='(ow-iw)/2', y='(oh-ih)/2')
            .output(output_path, vcodec='libx264', acodec='aac', preset='medium', crf=23)
            .overwrite_output()
            .run(capture_stdout=True, capture_stderr=True)
        )
    except ffmpeg.Error as e:
        logger.error(f"Error resizing video: {e.stderr.decode()}")
        raise
    
    return output_path

def crop_to_vertical(input_path: str, output_path: str, target_width: int = 1080, target_height: int = 1920,
                     num_samples: int = 12, window: dict = None) -> str:
    """
    Crop a video to vertical format (9:16) using face-aware cropping (Fase 4).

    Finds where the primary speaker's face sits across the clip (sampling
    several frames and taking the median position) and crops around it with
    headroom above the head, filling the full target frame with no
    letterboxing. Falls back to a plain center crop when no face is
    detected anywhere in the clip.

    Args:
        input_path: Path to input video
        output_path: Path to output video
        target_width: Target width (default 1080 for 9:16)
        target_height: Target height (default 1920 for 9:16)
        num_samples: Number of evenly-spaced frames sampled for face detection
        window: Precomputed crop window (from face_tracking.compute_vertical_crop_window),
            to avoid re-running face detection when the caller already has it
            (e.g. to also decide caption placement). Computed internally if not given.

    Returns:
        Path to the cropped video
    """
    from . import face_tracking

    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    if window is None:
        window = face_tracking.compute_vertical_crop_window(input_path, target_width, target_height, num_samples)
    logger.info(
        "Crop vertical: faces_detected=%d used_fallback=%s window=(x=%d, y=%d, w=%d, h=%d)",
        window["faces_detected"], window["used_fallback"],
        window["x"], window["y"], window["width"], window["height"],
    )

    try:
        # Building the output from a filtered video-only stream (crop+scale)
        # means only that stream reaches .output() -- the source's audio
        # stream has to be re-attached explicitly or it's silently dropped.
        probe = ffmpeg.probe(input_path)
        has_audio = any(s['codec_type'] == 'audio' for s in probe['streams'])

        input_stream = ffmpeg.input(input_path)
        video = (
            input_stream.video
            .filter('crop', w=window['width'], h=window['height'], x=window['x'], y=window['y'])
            .filter('scale', w=target_width, h=target_height)
        )

        if has_audio:
            output = ffmpeg.output(video, input_stream.audio, output_path,
                                   vcodec='libx264', acodec='aac', preset='medium', crf=23)
        else:
            output = ffmpeg.output(video, output_path, vcodec='libx264', preset='medium', crf=23)

        output.overwrite_output().run(capture_stdout=True, capture_stderr=True)
    except ffmpeg.Error as e:
        logger.error(f"Error cropping video to vertical: {e.stderr.decode()}")
        raise

    return output_path

def crop_to_vertical_blurred_background(input_path: str, output_path: str,
                                        target_width: int = 1080, target_height: int = 1920,
                                        blur_sigma: int = 20) -> str:
    """
    Fase 9 -- layout "fundo desfocado": mostra o video inteiro sem cortar a
    cena (encaixado por dentro do quadro vertical, sem letterbox visivel)
    sobre uma copia ampliada e borrada do mesmo video preenchendo o quadro.
    Pedido pelo usuario para videos sem pessoa aparecendo logo no inicio,
    onde o crop por rosto (Fase 4) cortaria cena demais.

    Args:
        input_path: Path to input video
        output_path: Path to output video
        target_width, target_height: Output vertical resolution
        blur_sigma: Gaussian blur strength applied to the background copy

    Returns:
        Path to the output video
    """
    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    try:
        probe = ffmpeg.probe(input_path)
        has_audio = any(s['codec_type'] == 'audio' for s in probe['streams'])

        input_stream = ffmpeg.input(input_path)
        split = input_stream.video.filter_multi_output('split')

        background = (
            split[0]
            .filter('scale', w=target_width, h=target_height, force_original_aspect_ratio='increase')
            .filter('crop', w=target_width, h=target_height)
            .filter('gblur', sigma=blur_sigma)
        )
        foreground = split[1].filter(
            'scale', w=target_width, h=target_height, force_original_aspect_ratio='decrease')

        composed = ffmpeg.filter([background, foreground], 'overlay', x='(W-w)/2', y='(H-h)/2')

        if has_audio:
            output = ffmpeg.output(composed, input_stream.audio, output_path,
                                   vcodec='libx264', acodec='aac', preset='medium', crf=23)
        else:
            output = ffmpeg.output(composed, output_path, vcodec='libx264', preset='medium', crf=23)

        output.overwrite_output().run(capture_stdout=True, capture_stderr=True)
    except ffmpeg.Error as e:
        logger.error(f"Error building blurred-background layout: {e.stderr.decode()}")
        raise

    return output_path

def crop_to_vertical_active_speaker(input_path: str, output_path: str, transcription: dict,
                                    target_width: int = 1080, target_height: int = 1920,
                                    min_window_duration: float = 3.0, samples_per_window: int = 8,
                                    use_lip_sync: bool = True) -> str:
    """
    Fase 9 -- crop vertical com troca de enquadramento por falante ativo
    com analise de movimento labial (lip-sync). Detecta o rosto ativo de forma
    independente em cada sub-janela delimitada por uma fronteira de fala real
    (nunca no meio de uma palavra) medindo a variabilidade e movimento da boca,
    e concatena os trechos resultantes.

    Args:
        input_path: Path to the already-cut clip
        output_path: Path to write the final cropped video
        transcription: Word-level transcription scoped to this clip (same
            shape used for captions -- segment-relative timestamps)
        target_width, target_height: Output vertical resolution
        min_window_duration: Minimum seconds per speaker window before a
            crop switch is allowed
        samples_per_window: Frames sampled for face detection within each window
        use_lip_sync: Whether to use lip movement analysis to select active speaker (default True)

    Returns:
        Path to the cropped video
    """
    from . import face_tracking

    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    windows = face_tracking.compute_active_speaker_windows(
        input_path, transcription, target_width, target_height,
        min_window_duration=min_window_duration, samples_per_window=samples_per_window,
        use_lip_sync=use_lip_sync,
    )
    logger.info("Active speaker (lip-sync=%s): %d janela(s) de crop", use_lip_sync, len(windows))

    if len(windows) == 1:
        # No benefit to the cut+concat machinery for a single window.
        return crop_to_vertical(input_path, output_path, target_width, target_height, window=windows[0])

    with tempfile.TemporaryDirectory(prefix="active_speaker_") as temp_dir:
        segment_paths = []
        for i, window in enumerate(windows):
            segment_cut_path = os.path.join(temp_dir, f"seg_{i}_cut.mp4")
            cut_video_segment(input_path, window["start"], window["end"], segment_cut_path)

            segment_cropped_path = os.path.join(temp_dir, f"seg_{i}_cropped.mp4")
            crop_to_vertical(segment_cut_path, segment_cropped_path, target_width, target_height, window=window)
            segment_paths.append(segment_cropped_path)

        try:
            concat_streams = []
            for segment_path in segment_paths:
                segment_input = ffmpeg.input(segment_path)
                concat_streams.append(segment_input.video)
                concat_streams.append(segment_input.audio)
            joined = ffmpeg.concat(*concat_streams, v=1, a=1).node

            (
                ffmpeg
                .output(joined[0], joined[1], output_path, vcodec='libx264', acodec='aac',
                       preset='medium', crf=23)
                .overwrite_output()
                .run(capture_stdout=True, capture_stderr=True)
            )
        except ffmpeg.Error as e:
            logger.error(f"Error concatenating active-speaker segments: {e.stderr.decode()}")
            raise

    return output_path

def crop_to_vertical_all_faces(input_path: str, output_path: str,
                               target_width: int = 1080, target_height: int = 1920,
                               num_samples: int = 12) -> str:
    """
    Fase 9 -- layout 'Manter todos os rostos' (empilhado / split-screen):
    Exibe os 2 rostos dominantes empilhados verticalmente no quadro 9:16 (1080x1920),
    cada um em um painel de 1080x960. Se apenas 1 rosto for detectado, recai para crop_to_vertical.
    """
    from . import face_tracking

    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    stacked_info = face_tracking.compute_stacked_faces_crop_windows(
        input_path, target_width, target_height, num_samples
    )

    if stacked_info["used_fallback"]:
        logger.info("Layout empilhado (all_faces): <2 rostos encontrados, usando fallback de 1 rosto")
        return crop_to_vertical(input_path, output_path, target_width, target_height, num_samples=num_samples)

    top_w = stacked_info["top_window"]
    bot_w = stacked_info["bottom_window"]
    panel_height = target_height // 2

    logger.info("Layout empilhado (all_faces): rendering top_window=%s, bottom_window=%s", top_w, bot_w)

    try:
        probe = ffmpeg.probe(input_path)
        has_audio = any(s['codec_type'] == 'audio' for s in probe['streams'])

        input_stream = ffmpeg.input(input_path)
        split = input_stream.video.filter_multi_output('split')

        top_panel = (
            split[0]
            .filter('crop', w=top_w['width'], h=top_w['height'], x=top_w['x'], y=top_w['y'])
            .filter('scale', w=target_width, h=panel_height)
        )
        bottom_panel = (
            split[1]
            .filter('crop', w=bot_w['width'], h=bot_w['height'], x=bot_w['x'], y=bot_w['y'])
            .filter('scale', w=target_width, h=panel_height)
        )

        stacked_video = ffmpeg.filter([top_panel, bottom_panel], 'vstack')

        if has_audio:
            output = ffmpeg.output(stacked_video, input_stream.audio, output_path,
                                   vcodec='libx264', acodec='aac', preset='medium', crf=23)
        else:
            output = ffmpeg.output(stacked_video, output_path, vcodec='libx264', preset='medium', crf=23)

        output.overwrite_output().run(capture_stdout=True, capture_stderr=True)
    except ffmpeg.Error as e:
        logger.error(f"Error building stacked all_faces layout: {e.stderr.decode()}")
        raise

    return output_path


def crop_to_vertical_card_frame(input_path: str, output_path: str,
                                target_width: int = 1080, target_height: int = 1920) -> str:
    """Moldura Social Card (inspirado em docs/examples -- estilo Twitter/Instagram):
    Mantém o vídeo original completo e sem cortes no centro, enquadrado dentro
    de uma moldura moderna com fundo plano elegante.
    """
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    try:
        probe = ffmpeg.probe(input_path)
        has_audio = any(s['codec_type'] == 'audio' for s in probe['streams'])
        duration = _duration_from_probe(probe)
        if duration <= 0:
            raise ValueError(f"Could not determine media duration for framed layout: {input_path}")

        input_stream = ffmpeg.input(input_path)
        background = ffmpeg.input(
            f'color=c=0x18181b:s={target_width}x{target_height}:d={duration:.6f}',
            f='lavfi',
        )

        card_video_w = int(round(target_width * 0.92))
        foreground = (
            input_stream.video
            .filter('scale', w=card_video_w, h=-1, force_original_aspect_ratio='decrease')
            .filter('setpts', 'PTS-STARTPTS')
        )

        composed = ffmpeg.filter(
            [background, foreground],
            'overlay',
            x='(W-w)/2',
            y='(H-h)/2',
        )

        output_options = {
            'vcodec': 'libx264',
            'preset': 'medium',
            'crf': 23,
            't': duration,
        }

        if has_audio:
            output = ffmpeg.output(composed, input_stream.audio, output_path,
                                   acodec='aac', **output_options)
        else:
            output = ffmpeg.output(composed, output_path, **output_options)

        output.overwrite_output().run(capture_stdout=True, capture_stderr=True)
    except ffmpeg.Error as e:
        logger.error(f"Error building card_frame layout: {e.stderr.decode()}")
        raise

    return output_path


def crop_to_vertical_header_footer_frame(input_path: str, output_path: str,
                                         target_width: int = 1080, target_height: int = 1920) -> str:
    """Moldura Editorial (inspirado em docs/examples -- Topo Título + Rodapé Marca):
    Vídeo completo e não-cortado centralizado no meio do quadro vertical 9:16,
    com áreas limpas no topo e rodapé preparadas para títulos e marcas.
    """
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    try:
        probe = ffmpeg.probe(input_path)
        has_audio = any(s['codec_type'] == 'audio' for s in probe['streams'])
        duration = _duration_from_probe(probe)
        if duration <= 0:
            raise ValueError(f"Could not determine media duration for framed layout: {input_path}")

        input_stream = ffmpeg.input(input_path)
        background = ffmpeg.input(
            f'color=c=0x0f172a:s={target_width}x{target_height}:d={duration:.6f}',
            f='lavfi',
        )

        foreground = (
            input_stream.video
            .filter('scale', w=target_width, h=-1, force_original_aspect_ratio='decrease')
            .filter('setpts', 'PTS-STARTPTS')
        )

        composed = ffmpeg.filter(
            [background, foreground],
            'overlay',
            x='(W-w)/2',
            y='(H-h)/2',
        )

        output_options = {
            'vcodec': 'libx264',
            'preset': 'medium',
            'crf': 23,
            't': duration,
        }

        if has_audio:
            output = ffmpeg.output(composed, input_stream.audio, output_path,
                                   acodec='aac', **output_options)
        else:
            output = ffmpeg.output(composed, output_path, **output_options)

        output.overwrite_output().run(capture_stdout=True, capture_stderr=True)
    except ffmpeg.Error as e:
        logger.error(f"Error building header_footer_frame layout: {e.stderr.decode()}")
        raise

    return output_path

def create_srt_from_segments(segments: list, output_path: str) -> str:
    """
    Create SRT file from transcription segments.
    
    Args:
        segments: List of segment dictionaries from transcription
        output_path: Path to save SRT file
    
    Returns:
        Path to the SRT file
    """
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    
    subtitles = []
    for i, segment in enumerate(segments):
        start_time = timedelta(seconds=segment['start'])
        end_time = timedelta(seconds=segment['end'])
        content = segment['text'].strip()
        
        subtitle = srt.Subtitle(
            index=i+1,
            start=start_time,
            end=end_time,
            content=content
        )
        subtitles.append(subtitle)
    
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(srt.compose(subtitles))
    
    return output_path

def add_subtitles(video_path: str, srt_path: str, output_path: str,
                  font: str = 'Arial', fontsize: int = 24,
                  fontcolor: str = 'white', box: bool = True,
                  boxcolor: str = 'black@0.5', boxborderw: int = 5) -> str:
    """
    Add subtitles to a video using FFmpeg subtitles filter.
    
    Args:
        video_path: Path to input video
        srt_path: Path to SRT file
        output_path: Path to output video
        font: Font family
        fontsize: Font size
        fontcolor: Font color (hex format without #, e.g., 'FFFFFF')
        box: Whether to draw a box behind text
        boxcolor: Box color (hex format without #, e.g., '000000')
        boxborderw: Box border width
    
    Returns:
        Path to the video with subtitles
    """
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    
    # Prepare font color (ASS format: &HBBGGRR&)
    # Font color in ASS is &HBBGGRR& where BB=blue, GG=green, RR=red, AA=alpha
    # Converting from RGB hex to BGR for ASS
    if fontcolor.startswith('#'):
        fontcolor = fontcolor[1:]
    if len(fontcolor) == 6:
        r = fontcolor[0:2]
        g = fontcolor[2:4]
        b = fontcolor[4:6]
        fontcolor_ass = f"&H{b}{g}{r}&"
    else:
        fontcolor_ass = "&HFFFFFF&"  # Default to white
    
    # Prepare box color
    if boxcolor.startswith('#'):
        boxcolor = boxcolor[1:]
    if len(boxcolor) == 6:
        r = boxcolor[0:2]
        g = boxcolor[2:4]
        b = boxcolor[4:6]
        if box:
            boxcolor_ass = f"&H{b}{g}{r}&"
        else:
            boxcolor_ass = "&H00000000&"  # Transparent
    else:
        if box:
            boxcolor_ass = "&H000000&"  # Default to black with full opacity
        else:
            boxcolor_ass = "&H00000000&"  # Transparent
    
    escaped_srt_path = escape_ffmpeg_filter_path(srt_path)

    try:
        (
            ffmpeg
            .input(video_path)
            .output(
                output_path,
                vf=f"subtitles=filename='{escaped_srt_path}':force_style='Fontname={font},Fontsize={fontsize},PrimaryColour={fontcolor_ass},BackColour={boxcolor_ass},BorderStyle={3 if box else 1},Outline={2 if box else 0},Shadow=0,MarginV=20'"
            )
            .overwrite_output()
            .run(capture_stdout=True, capture_stderr=True)
        )
    except ffmpeg.Error as e:
        logger.error(f"Error adding subtitles: {e.stderr.decode()}")
        raise

    return output_path

def add_styled_subtitles(video_path: str, ass_path: str, output_path: str) -> str:
    """
    Burn an .ass subtitle file (built via subtitles.build_ass_subtitle, Fase 5)
    into a video. Unlike add_subtitles' SRT+force_style path, all styling
    (colors, outline, shadow, karaoke) already lives in the .ass file itself.

    Args:
        video_path: Path to input video
        ass_path: Path to .ass subtitle file
        output_path: Path to output video

    Returns:
        Path to the video with styled subtitles
    """
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    escaped_ass_path = escape_ffmpeg_filter_path(ass_path)

    try:
        (
            ffmpeg
            .input(video_path)
            .output(output_path, vf=f"subtitles=filename='{escaped_ass_path}'")
            .overwrite_output()
            .run(capture_stdout=True, capture_stderr=True)
        )
    except ffmpeg.Error as e:
        logger.error(f"Error adding styled subtitles: {e.stderr.decode()}")
        raise

    return output_path

def add_watermark(video_path: str, watermark_path: str, output_path: str,
                  position: str = 'bottom-right', margin_x: int = 20, margin_y: int = 20,
                  opacity: float = 0.5, width: int = None) -> str:
    """
    Add a watermark image to a video.

    Args:
        video_path: Path to input video
        watermark_path: Path to watermark image (PNG with transparency)
        output_path: Path to output video
        position: Position of watermark (top-left, top-right, bottom-left, bottom-right, center)
        margin_x: Horizontal margin from edge
        margin_y: Vertical margin from edge
        opacity: Opacity of watermark (0.0 to 1.0)
        width: Optional target width in pixels for the watermark (aspect ratio preserved);
            left at native size when None

    Returns:
        Path to the video with watermark
    """
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    
    # Check if watermark file exists
    if not os.path.exists(watermark_path):
        # Create a simple text watermark as fallback
        watermark_path = create_default_watermark()
    
    # Position mapping. In the overlay filter's own expression evaluator,
    # uppercase W/H is the main (background) frame size and lowercase w/h is
    # the overlay (watermark) size -- `w-w`/`h-h` (same variable twice) always
    # evaluates to 0, which silently broke every position except top-left.
    pos_map = {
        'top-left': f'{margin_x}:{margin_y}',
        'top-right': f'(W-w-{margin_x}):{margin_y}',
        'bottom-left': f'{margin_x}:(H-h-{margin_y})',
        'bottom-right': f'(W-w-{margin_x}):(H-h-{margin_y})',
        'center': '(W-w)/2:(H-h)/2'
    }
    
    position = pos_map.get(position, pos_map['bottom-right'])

    try:
        # Split position into x and y components
        if ':' in position:
            x, y = position.split(':', 1)
        else:
            x, y = position, '0'

        # The overlay filter builds a new video-only stream; the source's
        # audio has to be re-attached explicitly to .output() or it's
        # silently dropped (same class of bug as crop_to_vertical).
        probe = ffmpeg.probe(video_path)
        has_audio = any(s['codec_type'] == 'audio' for s in probe['streams'])

        video_input = ffmpeg.input(video_path)
        watermark_input = ffmpeg.input(watermark_path)

        if width:
            watermark_input = watermark_input.filter('scale', w=width, h=-1)

        opacity = max(0.0, min(1.0, opacity))
        if opacity < 1.0:
            # colorchannelmixer needs an alpha channel to scale -- format=rgba
            # guarantees one even for watermark images without transparency.
            watermark_input = watermark_input.filter('format', 'rgba').filter('colorchannelmixer', aa=opacity)

        # Apply overlay filter which takes two inputs
        joined = ffmpeg.filter([video_input, watermark_input], 'overlay', x=x, y=y)

        if has_audio:
            output = ffmpeg.output(joined, video_input.audio, output_path, acodec='aac')
        else:
            output = ffmpeg.output(joined, output_path)

        output.overwrite_output().run(capture_stdout=True, capture_stderr=True)
    except ffmpeg.Error as e:
        logger.error(f"Error adding watermark: {e.stderr.decode()}")
        raise

    return output_path

def _default_windows_fontfile() -> str | None:
    """Points drawtext straight at a real TTF, bypassing fontconfig -- this
    Windows install has no default fontconfig config ("Fontconfig error:
    Cannot load default config file"), which silently broke every drawtext
    call (including the watermark fallback) before this fix."""
    windows_font = os.path.join(os.environ.get('WINDIR', 'C:/Windows'), 'Fonts', 'arial.ttf')
    return windows_font.replace('\\', '/') if os.path.exists(windows_font) else None

def create_default_watermark() -> str:
    """
    Create a default text watermark image.

    Returns:
        Path to the watermark image
    """
    watermark_dir = os.path.join(os.path.dirname(__file__), '..', 'assets')
    os.makedirs(watermark_dir, exist_ok=True)
    watermark_path = os.path.join(watermark_dir, 'watermark.png')

    # Create a simple text watermark using ImageMagick or PIL if available
    # For now, we'll create a simple colored rectangle as placeholder
    try:
        from PIL import Image, ImageDraw, ImageFont
        img = Image.new('RGBA', (200, 50), (255, 255, 255, 0))
        draw = ImageDraw.Draw(img)
        # Try to use a default font
        try:
            font = ImageFont.truetype("arial.ttf", 20)
        except IOError:
            font = ImageFont.load_default()
        draw.text((10, 15), "Cortaí", fill=(255, 255, 255, 128), font=font)
        img.save(watermark_path)
    except ImportError:
        # If PIL is not available, create a simple colored rectangle with ffmpeg.
        drawtext_kwargs = dict(text='Cortaí', fontcolor='black', fontsize=20, box=1, boxcolor='white@0.5')
        fontfile = _default_windows_fontfile()
        if fontfile:
            drawtext_kwargs['fontfile'] = fontfile

        (
            ffmpeg
            .input('color=c=white:s=200x50:d=1', f='lavfi')
            .drawtext(**drawtext_kwargs)
            .output(watermark_path, vframes=1)
            .overwrite_output()
            .run(capture_stdout=True, capture_stderr=True)
        )

    return watermark_path

def add_title_overlay(video_path: str, output_path: str, title: str = None, cta: str = None,
                      font_size: int = 48) -> str:
    """
    Burn an optional title banner (near the top) and/or CTA banner (near the
    bottom) into a video (Fase 6 -- templates de canal).

    If neither `title` nor `cta` is given, just copies the file through
    unchanged (drawtext needs at least one overlay to do anything).

    Args:
        video_path: Path to input video
        output_path: Path to output video
        title: Optional title text (rendered near the top)
        cta: Optional call-to-action text (rendered near the bottom)
        font_size: Base font size for the title; cta uses 80% of this

    Returns:
        Path to the output video
    """
    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    if not title and not cta:
        import shutil
        shutil.copy(video_path, output_path)
        return output_path

    fontfile = _default_windows_fontfile()
    probe = ffmpeg.probe(video_path)
    has_audio = any(s['codec_type'] == 'audio' for s in probe['streams'])

    input_stream = ffmpeg.input(video_path)
    video = input_stream.video

    if title:
        kwargs = dict(text=title, fontsize=font_size, fontcolor='white',
                      box=1, boxcolor='black@0.6', boxborderw=10,
                      x='(w-text_w)/2', y='h*0.04')
        if fontfile:
            kwargs['fontfile'] = fontfile
        video = video.drawtext(**kwargs)

    if cta:
        kwargs = dict(text=cta, fontsize=max(10, int(font_size * 0.8)), fontcolor='white',
                      box=1, boxcolor='black@0.6', boxborderw=10,
                      x='(w-text_w)/2', y='h*0.90')
        if fontfile:
            kwargs['fontfile'] = fontfile
        video = video.drawtext(**kwargs)

    try:
        if has_audio:
            output = ffmpeg.output(video, input_stream.audio, output_path, vcodec='libx264', acodec='aac')
        else:
            output = ffmpeg.output(video, output_path, vcodec='libx264')
        output.overwrite_output().run(capture_stdout=True, capture_stderr=True)
    except ffmpeg.Error as e:
        logger.error(f"Error adding title/cta overlay: {e.stderr.decode()}")
        raise

    return output_path

def export_video(input_path: str, output_path: str,
                 vcodec: str = 'libx264', acodec: str = 'aac',
                 preset: str = 'medium', crf: int = 23) -> str:
    """
    Export video with specified codecs.
    
    Args:
        input_path: Path to input video
        output_path: Path to output video
        vcodec: Video codec
        acodec: Audio codec
        preset: Encoding preset
        crf: Constant Rate Factor (quality)
    
    Returns:
        Path to the exported video
    """
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    
    try:
        (
            ffmpeg
            .input(input_path)
            .output(output_path, vcodec=vcodec, acodec=acodec, preset=preset, crf=crf)
            .overwrite_output()
            .run(capture_stdout=True, capture_stderr=True)
        )
    except ffmpeg.Error as e:
        logger.error(f"Error exporting video: {e.stderr.decode()}")
        raise
    
    return output_path

def render_vertical_clip(cut_video: str, final_path: str, temp_dir: str,
                         transcription: dict = None,
                         subtitles_path: str = None,
                         caption_preset: str = "white-bold",
                         casing_mode: str = "uppercase",
                         target_width: int = 1080,
                         target_height: int = 1920,
                         layout: str = "auto",
                         watermark_path: str = None,
                         watermark_position: str = "bottom-right",
                         watermark_opacity: float = 0.5,
                         watermark_margin_x: int = 20,
                         watermark_margin_y: int = 20,
                         watermark_width: int = None,
                         title: str = None,
                         cta: str = None,
                         vcodec: str = "libx264",
                         acodec: str = "aac",
                         preset: str = "medium",
                         crf: int = 23,
                         caption_font: str = "Arial",
                         crop_x_pct: float = None) -> str:
    if watermark_path is None:
        watermark_path = config.WATERMARK_IMAGE
    os.makedirs(os.path.dirname(final_path), exist_ok=True)
    os.makedirs(temp_dir, exist_ok=True)

    # Step 1: Determine the crop window once (respecting `layout`), then crop to vertical
    from . import face_tracking
    vertical_path = os.path.join(temp_dir, f"vertical_{os.path.basename(cut_video)}")
    logger.info("Cropping to vertical format (layout=%s)", layout)

    if crop_x_pct is not None:
        source_width, source_height = face_tracking.get_video_dimensions(cut_video)
        x, y, w, h = face_tracking.compute_positioned_crop_window(
            source_width, source_height, target_width, target_height, crop_x_pct)
        window = {"x": x, "y": y, "width": w, "height": h,
                  "source_width": source_width, "source_height": source_height,
                  "faces_detected": 0, "used_fallback": True, "median_face_height_ratio": 0.0}
        logger.info("Applying manual horizontal crop at %.1f%%", crop_x_pct)
        crop_to_vertical(cut_video, vertical_path, target_width, target_height, window=window)
    elif layout == "center":
        source_width, source_height = face_tracking.get_video_dimensions(cut_video)
        x, y, w, h = face_tracking.compute_center_crop_window(
            source_width, source_height, target_width, target_height)
        window = {"x": x, "y": y, "width": w, "height": h,
                  "source_width": source_width, "source_height": source_height,
                  "faces_detected": 0, "used_fallback": True, "median_face_height_ratio": 0.0}
        crop_to_vertical(cut_video, vertical_path, target_width, target_height, window=window)
    elif layout == "blurred_background":
        window = {"used_fallback": True, "median_face_height_ratio": 0.0}
        crop_to_vertical_blurred_background(cut_video, vertical_path, target_width, target_height)
    elif layout in ("all_faces", "stacked_faces"):
        window = {"used_fallback": True, "median_face_height_ratio": 0.0}
        crop_to_vertical_all_faces(cut_video, vertical_path, target_width, target_height)
    elif layout == "card_frame":
        window = {"used_fallback": True, "median_face_height_ratio": 0.0}
        crop_to_vertical_card_frame(cut_video, vertical_path, target_width, target_height)
    elif layout == "header_footer_frame":
        window = {"used_fallback": True, "median_face_height_ratio": 0.0}
        crop_to_vertical_header_footer_frame(cut_video, vertical_path, target_width, target_height)
    elif layout == "active_speaker" and transcription is not None:
        window = face_tracking.compute_vertical_crop_window(cut_video, target_width, target_height)
        crop_to_vertical_active_speaker(cut_video, vertical_path, transcription, target_width, target_height)
    else:
        if layout == "active_speaker":
            logger.warning("layout='active_speaker' pedido sem transcricao disponivel; usando 'auto'.")
        window = face_tracking.compute_vertical_crop_window(cut_video, target_width, target_height)
        crop_to_vertical(cut_video, vertical_path, target_width, target_height, window=window)

    # Step 2: Add title/CTA banners (Fase 6), if requested
    if title or cta:
        titled_path = os.path.join(temp_dir, f"titled_{os.path.basename(vertical_path)}")
        logger.info("Adding title/CTA overlay")
        add_title_overlay(vertical_path, titled_path, title=title, cta=cta)
    else:
        titled_path = vertical_path

    # Step 3: Add subtitles
    if subtitles_path is not None and os.path.exists(subtitles_path):
        subtitled_path = os.path.join(temp_dir, f"subtitled_{os.path.basename(titled_path)}")
        logger.info("Adding subtitles (SRT fornecido)")
        add_subtitles(titled_path, subtitles_path, subtitled_path)
    elif transcription is not None:
        from .subtitles import build_ass_subtitle
        position = "top" if (not window["used_fallback"] and window["median_face_height_ratio"] > 0.35) else "bottom"
        logger.info(
            "Gerando legendas estilizadas (preset=%s, font=%s, casing=%s, position=%s)",
            caption_preset, caption_font, casing_mode, position,
        )
        ass_path = os.path.join(temp_dir, "captions.ass")
        build_ass_subtitle(
            transcription, caption_preset, target_width, target_height, ass_path,
            position=position, casing_mode=casing_mode, font_name=caption_font,
        )
        subtitled_path = os.path.join(temp_dir, f"subtitled_{os.path.basename(titled_path)}")
        add_styled_subtitles(titled_path, ass_path, subtitled_path)
    else:
        subtitled_path = titled_path

    # Step 4: Add watermark
    if watermark_path and os.path.exists(watermark_path):
        watermarked_path = os.path.join(temp_dir, f"watermarked_{os.path.basename(subtitled_path)}")
        logger.info("Adding watermark")
        add_watermark(subtitled_path, watermark_path, watermarked_path,
                      position=watermark_position, margin_x=watermark_margin_x,
                      margin_y=watermark_margin_y, opacity=watermark_opacity, width=watermark_width)
    else:
        watermarked_path = subtitled_path

    # Step 5: Export final video
    logger.info("Exporting final video (vcodec=%s, preset=%s, crf=%s)", vcodec, preset, crf)
    export_video(watermarked_path, final_path, vcodec=vcodec, acodec=acodec, preset=preset, crf=crf)

    return final_path

def process_video_to_clip(url: str, start_time: float, end_time: float,
                         output_dir: str = None,
                         watermark_path: str = None,
                         subtitles_path: str = None,
                         caption_preset: str = "white-bold",
                         target_width: int = 1080,
                         target_height: int = 1920,
                         layout: str = "auto",
                         watermark_position: str = "bottom-right",
                         watermark_opacity: float = 0.5,
                         watermark_margin_x: int = 20,
                         watermark_margin_y: int = 20,
                         watermark_width: int = None,
                         title: str = None,
                         cta: str = None) -> str:
    """
    Full pipeline to convert a YouTube video to a vertical clip with subtitles and watermark:
    download -> cut -> transcribe the cut segment -> render_vertical_clip().

    See `render_vertical_clip` for the meaning of every argument beyond
    `url`, `start_time`, `end_time`, and `output_dir`.

    Returns:
        Path to the final video file
    """
    if output_dir is None:
        output_dir = config.OUTPUT_DIR
    os.makedirs(output_dir, exist_ok=True)

    # Generate unique job ID for temporary files
    job_id = str(int(__import__('time').time() * 1000))
    temp_dir = os.path.join(config.TEMP_DIR, f"job_{job_id}")
    os.makedirs(temp_dir, exist_ok=True)

    try:
        # Step 1: Download video
        logger.info(f"Downloading video from {url}")
        video_path = download_video(url)

        # Step 2: Cut segment
        cut_path = os.path.join(temp_dir, f"cut_{os.path.basename(video_path)}")
        logger.info(f"Cutting segment from {start_time} to {end_time}")
        cut_video = cut_video_segment(video_path, start_time, end_time, cut_path)

        # Step 3: Transcribe audio (only when no ready-made SRT was supplied)
        transcription = None
        if subtitles_path is None:
            logger.info("Transcribing audio")
            audio_path = os.path.join(temp_dir, "audio.wav")
            extract_audio(cut_video, audio_path)
            transcription = transcribe_audio(audio_path)

        final_path = os.path.join(output_dir, f"clip_{job_id}.mp4")
        return render_vertical_clip(
            cut_video, final_path, temp_dir,
            transcription=transcription, subtitles_path=subtitles_path,
            caption_preset=caption_preset, target_width=target_width, target_height=target_height,
            layout=layout, watermark_path=watermark_path, watermark_position=watermark_position,
            watermark_opacity=watermark_opacity, watermark_margin_x=watermark_margin_x,
            watermark_margin_y=watermark_margin_y, watermark_width=watermark_width,
            title=title, cta=cta,
        )

    finally:
        # Clean up temporary files
        import shutil
        try:
            shutil.rmtree(temp_dir)
        except Exception as e:
            logger.warning(f"Could not clean up temp directory {temp_dir}: {e}")

def render_clip_from_source(video_path: str, full_transcription: dict, start: float, end: float,
                            output_dir: str, output_filename: str,
                            vocabulary: list = None, word_overrides: dict = None,
                            **render_kwargs) -> str:
    """
    Fase 7: renders ONE clip out of an ALREADY-DOWNLOADED video and an
    ALREADY-COMPUTED full-video transcription -- never re-downloads or
    re-transcribes. Used both for the initial batch of suggested clips and
    for re-rendering a single clip with a different template.

    Args:
        video_path: Path to the already-downloaded source video
        full_transcription: Full-video transcription (from transcription.transcribe_video_file)
        start, end: Segment boundaries in seconds (from a clip suggestion)
        output_dir: Directory to write the final clip into
        output_filename: Final file name (e.g. f"clip_{clip_id}.mp4")
        vocabulary: Optional list of correct terms (e.g. ["ANAC", "VOEPASS"]) used to
            auto-correct misheard acronyms/proper nouns in the captions (Fase 9)
        word_overrides: Optional {word_index: text} manual corrections, indexed into
            this CLIP's own flattened word list (after slicing, not the full video's) --
            always applied after vocabulary auto-correction, so manual edits always win
        **render_kwargs: Forwarded to render_vertical_clip (caption_preset, layout,
            watermark_*, title, cta, target_width, target_height)

    Returns:
        Path to the final video file
    """
    from .segmentation import slice_transcription
    from .vocabulary import apply_corrections

    os.makedirs(output_dir, exist_ok=True)
    temp_dir = os.path.join(config.TEMP_DIR, f"rerender_{uuid.uuid4().hex}")
    os.makedirs(temp_dir, exist_ok=True)

    try:
        cut_path = os.path.join(temp_dir, f"cut_{os.path.basename(video_path)}")
        cut_video = cut_video_segment(video_path, start, end, cut_path)

        sliced_transcription = slice_transcription(full_transcription, start, end)
        sliced_transcription = apply_corrections(sliced_transcription, vocabulary, word_overrides)
        final_path = os.path.join(output_dir, output_filename)

        return render_vertical_clip(cut_video, final_path, temp_dir,
                                    transcription=sliced_transcription, **render_kwargs)
    finally:
        import shutil
        try:
            shutil.rmtree(temp_dir)
        except Exception as e:
            logger.warning(f"Could not clean up temp directory {temp_dir}: {e}")
