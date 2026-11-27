"""Deteccao de rosto local (Fase 4) para crop inteligente vertical.

Usa `cv2.FaceDetectorYN` (modelo YuNet, ONNX, bundled em
`models/face_detection_yunet_2023mar.onnx`) -- a API atual do OpenCV para
deteccao de rosto: `cv2.CascadeClassifier` (Haar cascade) nao esta mais
disponivel nos bindings Python do OpenCV instalado neste projeto.

Produz um UNICO crop estavel por clipe (nao um crop dinamico por frame): a
mediana das posicoes de rosto detectadas ao longo do clipe e usada como
centro do crop, o que ja evita qualquer "crop nervoso" por construcao --
nao ha nada para tremer se o crop nao se move. Rastreamento dinamico por
frame (ex: para cortes muito longos com movimento real de camera) fica
registrado como evolucao futura em vez de reescrever o pipeline agora.
"""
import logging
import math
import os
import statistics
from dataclasses import dataclass, field

import cv2
import numpy as np

logger = logging.getLogger(__name__)

_MODEL_PATH = os.path.join(os.path.dirname(__file__), "models", "face_detection_yunet_2023mar.onnx")
_SCORE_THRESHOLD = 0.7


@dataclass
class FaceSample:
    timestamp: float
    x: float
    y: float
    w: float
    h: float
    confidence: float


@dataclass
class FaceTrack:
    track_id: int
    samples: list[FaceSample] = field(default_factory=list)
    mouth_rois: list[np.ndarray] = field(default_factory=list)
    mouth_widths: list[float] = field(default_factory=list)

    @property
    def median_area(self) -> float:
        return statistics.median(s.w * s.h for s in self.samples) if self.samples else 0.0

    @property
    def median_center(self) -> tuple[float, float]:
        if not self.samples:
            return (0.0, 0.0)
        cx = statistics.median(s.x + s.w / 2.0 for s in self.samples)
        cy = statistics.median(s.y + s.h / 2.0 for s in self.samples)
        return (cx, cy)


def _create_detector(frame_width: int, frame_height: int) -> "cv2.FaceDetectorYN":
    return cv2.FaceDetectorYN_create(_MODEL_PATH, "", (frame_width, frame_height),
                                      score_threshold=_SCORE_THRESHOLD)


def detect_largest_face(detector, frame) -> tuple[float, float, float, float, float] | None:
    """Returns (x, y, w, h, confidence) of the largest detected face, or None."""
    retval, faces = detector.detect(frame)
    if faces is None or len(faces) == 0:
        return None
    # Each row: x, y, w, h, <5 landmark pairs>, confidence
    best = max(faces, key=lambda f: f[2] * f[3])
    x, y, w, h = best[0], best[1], best[2], best[3]
    confidence = best[-1]
    return float(x), float(y), float(w), float(h), float(confidence)


def detect_all_faces(detector, frame) -> list[tuple[float, float, float, float, float, list[float]]]:
    """Returns a list of (x, y, w, h, confidence, landmarks) for all detected faces in frame.
    Landmarks is a list of 10 floats: [right_eye_x, right_eye_y, left_eye_x, left_eye_y,
                                       nose_tip_x, nose_tip_y, right_mouth_x, right_mouth_y,
                                       left_mouth_x, left_mouth_y].
    """
    retval, faces = detector.detect(frame)
    if faces is None or len(faces) == 0:
        return []
    results = []
    for f in faces:
        x, y, w, h = float(f[0]), float(f[1]), float(f[2]), float(f[3])
        landmarks = [float(v) for v in f[4:14]]
        confidence = float(f[14])
        results.append((x, y, w, h, confidence, landmarks))
    return results


def extract_mouth_roi(frame: np.ndarray, x: float, y: float, w: float, h: float,
                      landmarks: list[float], target_size: tuple[int, int] = (32, 32)) -> tuple[np.ndarray | None, float]:
    """Extracts a normalized mouth region crop from frame and computes mouth width in pixels."""
    if not landmarks or len(landmarks) < 10:
        mcx = x + w * 0.5
        mcy = y + h * 0.75
        mw = w * 0.4
        mh = h * 0.25
        mouth_width = mw
    else:
        # YuNet landmarks: [right_eye, left_eye, nose_tip, right_mouth, left_mouth]
        # Landmarks list indices:
        # 0,1: right eye; 2,3: left eye; 4,5: nose tip; 6,7: right mouth corner; 8,9: left mouth corner
        rmx, rmy = landmarks[6], landmarks[7]
        lmx, lmy = landmarks[8], landmarks[9]
        mouth_width = float(math.hypot(lmx - rmx, lmy - rmy))

        mcx = (rmx + lmx) / 2.0
        mcy = (rmy + lmy) / 2.0

        mw = max(10.0, mouth_width * 1.5)
        mh = max(10.0, mouth_width * 1.1)

    fh, fw = frame.shape[:2]
    ix1 = max(0, int(round(mcx - mw / 2.0)))
    iy1 = max(0, int(round(mcy - mh / 2.0)))
    ix2 = min(fw, int(round(mcx + mw / 2.0)))
    iy2 = min(fh, int(round(mcy + mh / 2.0)))

    if ix2 <= ix1 or iy2 <= iy1:
        return None, mouth_width

    crop = frame[iy1:iy2, ix1:ix2]
    if crop.size == 0:
        return None, mouth_width

    resized = cv2.resize(crop, target_size, interpolation=cv2.INTER_AREA)
    return resized, mouth_width


def compute_lip_movement_score(mouth_rois: list[np.ndarray], mouth_widths: list[float]) -> float:
    """Calculates a lip motion activity score across time samples for a face track.
    Combines frame-to-frame pixel intensity change in mouth ROI and variation in mouth width.
    """
    valid_rois = [r for r in mouth_rois if r is not None]
    if len(valid_rois) < 2:
        return 0.0

    # 1. Width variance
    valid_widths = [w for w in mouth_widths if w > 0]
    width_var = float(np.var(valid_widths)) if len(valid_widths) > 1 else 0.0

    # 2. Frame-to-frame pixel motion
    pixel_diffs = []
    for i in range(1, len(valid_rois)):
        g1 = cv2.cvtColor(valid_rois[i - 1], cv2.COLOR_BGR2GRAY)
        g2 = cv2.cvtColor(valid_rois[i], cv2.COLOR_BGR2GRAY)
        diff = float(np.mean(cv2.absdiff(g1, g2)))
        pixel_diffs.append(diff)

    motion_var = float(np.mean(pixel_diffs)) if pixel_diffs else 0.0
    score = motion_var * 0.7 + width_var * 0.3
    return score


def track_faces_in_window(video_path: str, start: float = None, end: float = None,
                         num_samples: int = 8) -> list[FaceTrack]:
    """Samples `num_samples` frames in [start, end], detects all faces, and
    groups them into tracks across frames based on centroid distance."""
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        raise ValueError(f"Nao foi possivel abrir o video para deteccao de rosto: {video_path}")

    try:
        fps = cap.get(cv2.CAP_PROP_FPS) or 0
        frame_count = cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0
        video_duration = frame_count / fps if fps > 0 else 0
        if video_duration <= 0:
            return []

        range_start = 0.0 if start is None else max(0.0, start)
        range_end = video_duration if end is None else min(video_duration, end)
        window_duration = range_end - range_start
        if window_duration <= 0:
            return []

        frame_width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        frame_height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        detector = _create_detector(frame_width, frame_height)

        tracks: list[FaceTrack] = []
        next_track_id = 0

        for i in range(num_samples):
            timestamp = range_start + window_duration * (i + 0.5) / num_samples
            cap.set(cv2.CAP_PROP_POS_MSEC, timestamp * 1000)
            ok, frame = cap.read()
            if not ok or frame is None:
                continue

            detected_faces = detect_all_faces(detector, frame)
            for fx, fy, fw, fh, confidence, landmarks in detected_faces:
                sample = FaceSample(timestamp=timestamp, x=fx, y=fy, w=fw, h=fh, confidence=confidence)
                mouth_roi, mouth_width = extract_mouth_roi(frame, fx, fy, fw, fh, landmarks)

                cx = fx + fw / 2.0
                cy = fy + fh / 2.0

                # Match sample to existing track by centroid distance
                best_track = None
                best_dist = float("inf")
                for track in tracks:
                    tcx, tcy = track.median_center
                    dist = math.hypot(cx - tcx, cy - tcy)
                    max_allowed = max(fw, fh) * 0.8
                    if dist < max_allowed and dist < best_dist:
                        best_dist = dist
                        best_track = track

                if best_track is None:
                    new_track = FaceTrack(track_id=next_track_id)
                    next_track_id += 1
                    new_track.samples.append(sample)
                    new_track.mouth_rois.append(mouth_roi)
                    new_track.mouth_widths.append(mouth_width)
                    tracks.append(new_track)
                else:
                    best_track.samples.append(sample)
                    best_track.mouth_rois.append(mouth_roi)
                    best_track.mouth_widths.append(mouth_width)

        return tracks
    finally:
        cap.release()



def sample_face_track(video_path: str, num_samples: int = 12,
                       start: float = None, end: float = None) -> list[FaceSample]:
    """Samples `num_samples` evenly-spaced frames and runs face detection on
    each. Frames with no detected face are skipped.

    `start`/`end` (seconds) restrict sampling to a sub-range of the video --
    used by the active-speaker heuristic (Fase 9) to detect the dominant
    face independently within each speech-boundary window, instead of
    across the whole clip."""
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        raise ValueError(f"Nao foi possivel abrir o video para deteccao de rosto: {video_path}")

    try:
        fps = cap.get(cv2.CAP_PROP_FPS) or 0
        frame_count = cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0
        video_duration = frame_count / fps if fps > 0 else 0
        if video_duration <= 0:
            return []

        range_start = 0.0 if start is None else max(0.0, start)
        range_end = video_duration if end is None else min(video_duration, end)
        window_duration = range_end - range_start
        if window_duration <= 0:
            return []

        frame_width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        frame_height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        detector = _create_detector(frame_width, frame_height)

        samples = []
        for i in range(num_samples):
            timestamp = range_start + window_duration * (i + 0.5) / num_samples
            cap.set(cv2.CAP_PROP_POS_MSEC, timestamp * 1000)
            ok, frame = cap.read()
            if not ok or frame is None:
                continue
            face = detect_largest_face(detector, frame)
            if face is None:
                continue
            x, y, w, h, confidence = face
            samples.append(FaceSample(timestamp=timestamp, x=x, y=y, w=w, h=h, confidence=confidence))
        return samples
    finally:
        cap.release()


def get_video_dimensions(video_path: str) -> tuple[int, int]:
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        raise ValueError(f"Nao foi possivel abrir o video: {video_path}")
    try:
        width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        return width, height
    finally:
        cap.release()


def compute_center_crop_window(source_width: int, source_height: int,
                                target_width: int = 1080, target_height: int = 1920) -> tuple[int, int, int, int]:
    """Pure geometric center crop to the target aspect ratio -- fallback when
    no face is detected anywhere in the clip."""
    target_aspect = target_width / target_height
    if source_width / source_height > target_aspect:
        crop_h = source_height
        crop_w = int(round(crop_h * target_aspect))
    else:
        crop_w = source_width
        crop_h = int(round(crop_w / target_aspect))
    x = (source_width - crop_w) // 2
    y = (source_height - crop_h) // 2
    return x, y, crop_w, crop_h


def compute_positioned_crop_window(
    source_width: int,
    source_height: int,
    target_width: int = 1080,
    target_height: int = 1920,
    crop_x_pct: float = 50.0,
) -> tuple[int, int, int, int]:
    """Crop to the target aspect ratio at a user-selected horizontal point.

    ``crop_x_pct`` maps the available horizontal travel exactly: 0 is the
    left edge, 50 is centered, and 100 is the right edge. The vertical axis
    stays centered. If the source only has vertical overflow, horizontal
    travel is zero and every valid percentage correctly resolves to x=0.
    """
    if min(source_width, source_height, target_width, target_height) <= 0:
        raise ValueError("Dimensoes de origem e destino devem ser positivas")
    try:
        crop_x_pct = float(crop_x_pct)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"crop_x_pct invalido: {crop_x_pct!r}") from exc
    if not math.isfinite(crop_x_pct) or not 0.0 <= crop_x_pct <= 100.0:
        raise ValueError(f"crop_x_pct deve estar entre 0 e 100: {crop_x_pct!r}")

    _, y, crop_w, crop_h = compute_center_crop_window(
        source_width, source_height, target_width, target_height
    )
    max_x = max(0, source_width - crop_w)
    x = int(round(max_x * crop_x_pct / 100.0))
    return x, y, crop_w, crop_h


def compute_face_crop_window(samples: list[FaceSample], source_width: int, source_height: int,
                              target_width: int = 1080, target_height: int = 1920,
                              headroom_ratio: float = 0.12) -> tuple[int, int, int, int]:
    """Single stable crop window for the whole clip: centered horizontally on
    the median detected face position, positioned vertically to leave
    headroom above the head. The median (rather than the mean) keeps a
    handful of false-positive or off-center detections from skewing the
    crop -- the closest thing to a dead zone that makes sense for a single
    per-clip crop rather than a per-frame one."""
    if not samples:
        return compute_center_crop_window(source_width, source_height, target_width, target_height)

    target_aspect = target_width / target_height
    if source_width / source_height > target_aspect:
        crop_h = source_height
        crop_w = int(round(crop_h * target_aspect))
    else:
        crop_w = source_width
        crop_h = int(round(crop_w / target_aspect))

    median_cx = statistics.median(s.x + s.w / 2.0 for s in samples)
    median_left = statistics.median(s.x for s in samples)
    median_right = statistics.median(s.x + s.w for s in samples)
    median_top = statistics.median(s.y for s in samples)

    x = int(round(median_cx - crop_w / 2.0))
    y = int(round(median_top - headroom_ratio * crop_h))

    # Safety bounds: Ensure face left/right are not clipped off
    margin_x = int(round(crop_w * 0.05))
    if x > median_left - margin_x:
        x = int(round(median_left - margin_x))
    if x + crop_w < median_right + margin_x:
        x = int(round(median_right + margin_x - crop_w))

    x = max(0, min(x, source_width - crop_w))
    y = max(0, min(y, source_height - crop_h))
    return x, y, crop_w, crop_h


def compute_vertical_crop_window(video_path: str, target_width: int = 1080, target_height: int = 1920,
                                  num_samples: int = 12) -> dict:
    """Entry point for Fase 4: the crop window to use for the whole clip,
    plus whether a face was actually found (fallback flag)."""
    source_width, source_height = get_video_dimensions(video_path)
    samples = sample_face_track(video_path, num_samples=num_samples)

    if samples:
        x, y, w, h = compute_face_crop_window(samples, source_width, source_height, target_width, target_height)
        used_fallback = False
        median_face_height_ratio = statistics.median(s.h for s in samples) / h
    else:
        x, y, w, h = compute_center_crop_window(source_width, source_height, target_width, target_height)
        used_fallback = True
        median_face_height_ratio = 0.0

    return {
        "x": x, "y": y, "width": w, "height": h,
        "source_width": source_width, "source_height": source_height,
        "faces_detected": len(samples),
        "used_fallback": used_fallback,
        "median_face_height_ratio": median_face_height_ratio,
    }


def build_speech_windows(words: list[dict], boundary_indices: set[int], total_duration: float,
                          min_window_duration: float = 3.0) -> list[tuple[float, float]]:
    """Pure windowing logic behind active speaker detection: splits the clip
    into (start, end) ranges of at least `min_window_duration` seconds,
    always cutting exactly on a speech boundary (never mid-word).

    Windows are always contiguous and span the full `[0, total_duration]`
    range -- real speech has real pauses between boundary words, so a
    window can't simply run word-end to next-word-start, or that silent gap
    would be dropped entirely when segments are cut and concatenated,
    shortening the video and desyncing captions from the rest of the
    pipeline (which times everything against the original, un-shortened
    clip). Each cut point is the boundary word's end time; the pause after
    it is folded into the following window instead of vanishing.

    Separated from `compute_active_speaker_windows` so the boundary math is
    testable without a real video file or the cv2 face detector.
    """
    if not words:
        return []

    last_idx = len(words) - 1
    cut_points: list[float] = []
    window_start_idx = 0

    for i, word in enumerate(words):
        is_last_word = i == last_idx
        if i not in boundary_indices and not is_last_word:
            continue
        duration = word["end"] - words[window_start_idx]["start"]
        if duration < min_window_duration and not is_last_word:
            continue
        cut_points.append(word["end"])
        window_start_idx = i + 1

    if not cut_points:
        cut_points = [total_duration]
    else:
        cut_points[-1] = total_duration  # cover any trailing silence too

    windows = []
    prev_end = 0.0
    for cut in cut_points:
        windows.append((prev_end, cut))
        prev_end = cut

    # A too-short final window (e.g. one trailing word after the last real
    # boundary) would cause a jarring crop switch for a fraction of a
    # second -- merge it into the previous window instead.
    if len(windows) >= 2 and (windows[-1][1] - windows[-1][0]) < min_window_duration:
        prev_start, _ = windows[-2]
        windows[-2] = (prev_start, windows[-1][1])
        windows.pop()

    return windows


def compute_active_speaker_windows(video_path: str, transcription: dict,
                                    target_width: int = 1080, target_height: int = 1920,
                                    min_window_duration: float = 3.0,
                                    samples_per_window: int = 8,
                                    use_lip_sync: bool = True) -> list[dict]:
    """Fase 9 -- active speaker detection com analise de movimento labial (lip-sync):
    divide o clipe em sub-janelas de pelo menos `min_window_duration` segundos,
    sempre cortando em uma fronteira de fala real (`segmentation.find_boundary_indices`)
    e detecta o rosto com maior atividade labial (variacao de boca entre frames) em cada sub-janela.

    Returns a list of dicts, each with start/end (seconds, relative to `video_path`)
    plus the same fields as `compute_vertical_crop_window`.
    """
    from .segmentation import flatten_words, find_boundary_indices

    source_width, source_height = get_video_dimensions(video_path)
    words = flatten_words(transcription)

    cap = cv2.VideoCapture(video_path)
    fps = cap.get(cv2.CAP_PROP_FPS) or 0
    frame_count = cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0
    video_duration = frame_count / fps if fps > 0 else 0
    cap.release()

    if not words:
        # No transcript to anchor window boundaries on -- fall back to a
        # single window covering the whole clip (same as Fase 4's "auto").
        window = compute_vertical_crop_window(video_path, target_width, target_height)
        return [{**window, "start": 0.0, "end": video_duration}]

    boundary_indices = find_boundary_indices(words)
    windows = build_speech_windows(words, boundary_indices, video_duration, min_window_duration)

    results = []
    for window_start, window_end in windows:
        if use_lip_sync:
            tracks = track_faces_in_window(video_path, start=window_start, end=window_end,
                                           num_samples=samples_per_window)
            if tracks:
                scored_tracks = [
                    (t, compute_lip_movement_score(t.mouth_rois, t.mouth_widths))
                    for t in tracks if t.samples
                ]
                scored_tracks.sort(key=lambda item: item[1], reverse=True)
                top_track, top_score = scored_tracks[0]

                # If all scores are 0, fall back to largest face
                if top_score <= 0.0 and len(scored_tracks) > 1:
                    top_track = max(tracks, key=lambda t: t.median_area)

                samples = top_track.samples
                logger.info("Janela [%.2fs - %.2fs]: selecionado rosto track %d com lip_score=%.4f (area_mediana=%.0f)",
                            window_start, window_end, top_track.track_id, top_score, top_track.median_area)
            else:
                samples = []
        else:
            samples = sample_face_track(video_path, num_samples=samples_per_window,
                                        start=window_start, end=window_end)

        if samples:
            x, y, w, h = compute_face_crop_window(samples, source_width, source_height,
                                                   target_width, target_height)
            used_fallback = False
        else:
            x, y, w, h = compute_center_crop_window(source_width, source_height,
                                                     target_width, target_height)
            used_fallback = True

        results.append({
            "start": window_start, "end": window_end,
            "x": x, "y": y, "width": w, "height": h,
            "source_width": source_width, "source_height": source_height,
            "faces_detected": len(samples), "used_fallback": used_fallback,
        })

    return results


def compute_panel_crop_window(samples: list[FaceSample], source_width: int, source_height: int,
                              panel_width: int = 1080, panel_height: int = 960,
                              headroom_ratio: float = 0.1) -> tuple[int, int, int, int]:
    """Calculates crop window for a single panel in a stacked layout (default aspect 1080:960 = 1.125)."""
    if not samples:
        target_aspect = panel_width / panel_height
        if source_width / source_height > target_aspect:
            crop_h = source_height
            crop_w = int(round(crop_h * target_aspect))
        else:
            crop_w = source_width
            crop_h = int(round(crop_w / target_aspect))
        x = (source_width - crop_w) // 2
        y = (source_height - crop_h) // 2
        return x, y, crop_w, crop_h

    target_aspect = panel_width / panel_height
    if source_width / source_height > target_aspect:
        crop_h = source_height
        crop_w = int(round(crop_h * target_aspect))
    else:
        crop_w = source_width
        crop_h = int(round(crop_w / target_aspect))

    median_cx = statistics.median(s.x + s.w / 2.0 for s in samples)
    median_top = statistics.median(s.y for s in samples)

    x = int(round(median_cx - crop_w / 2.0))
    y = int(round(median_top - headroom_ratio * crop_h))

    x = max(0, min(x, source_width - crop_w))
    y = max(0, min(y, source_height - crop_h))
    return x, y, crop_w, crop_h


def compute_stacked_faces_crop_windows(video_path: str, target_width: int = 1080,
                                         target_height: int = 1920, num_samples: int = 12) -> dict:
    """Computes two panel crop windows (top and bottom, 1080x960 each) for the two primary faces
    detected in the video for layout='all_faces'. If fewer than 2 distinct faces exist, returns used_fallback=True.
    """
    source_width, source_height = get_video_dimensions(video_path)
    panel_height = target_height // 2

    tracks = track_faces_in_window(video_path, num_samples=num_samples)
    valid_tracks = [t for t in tracks if len(t.samples) >= 1]

    if len(valid_tracks) < 2:
        top_x, top_y, top_w, top_h = compute_panel_crop_window([], source_width, source_height, target_width, panel_height)
        bottom_x, bottom_y, bottom_w, bottom_h = compute_panel_crop_window([], source_width, source_height, target_width, panel_height)
        return {
            "used_fallback": True,
            "faces_detected": len(valid_tracks),
            "top_window": {"x": top_x, "y": top_y, "width": top_w, "height": top_h},
            "bottom_window": {"x": bottom_x, "y": bottom_y, "width": bottom_w, "height": bottom_h},
        }

    # Sort tracks by face area/presence to get the top 2
    valid_tracks.sort(key=lambda t: t.median_area, reverse=True)
    top_two = valid_tracks[:2]

    # Sort top 2 left-to-right (horizontal position)
    top_two.sort(key=lambda t: t.median_center[0])
    left_track, right_track = top_two[0], top_two[1]

    tx, ty, tw, th = compute_panel_crop_window(left_track.samples, source_width, source_height, target_width, panel_height)
    bx, by, bw, bh = compute_panel_crop_window(right_track.samples, source_width, source_height, target_width, panel_height)

    return {
        "used_fallback": False,
        "faces_detected": len(valid_tracks),
        "top_window": {"x": tx, "y": ty, "width": tw, "height": th},
        "bottom_window": {"x": bx, "y": by, "width": bw, "height": bh},
    }

