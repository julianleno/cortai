"""Deterministic clip-candidate segmentation (Fase 2 — sem IA externa ainda).

Turns a full-video transcription (with word-level timestamps) into a list
of non-overlapping candidate clips whose start and end always land exactly
on a word boundary, and never mid-sentence: a candidate can only start at
the very first word or right after a detected boundary, and can only end on
a detected boundary.

A boundary is a word that either ends with sentence punctuation (. ! ?) or
is followed by a pause of at least `pause_threshold` seconds — real speech
transcriptions frequently under-punctuate (e.g. a spoken period rendered as
a comma), so the pause signal is what makes this robust in practice.
"""
from dataclasses import dataclass, field

SENTENCE_END_CHARS = (".", "!", "?")
DEFAULT_PAUSE_THRESHOLD = 0.35  # seconds


@dataclass
class ClipCandidate:
    start: float
    end: float
    text: str
    word_start_index: int
    word_end_index: int

    @property
    def duration(self) -> float:
        return self.end - self.start


def categorize_local_text(text: str) -> dict:
    """Análise léxica determinística local para categorização de momentos de vídeo."""
    txt_lower = text.lower()
    import re

    # 1. Dados / Ensinamento Numérico (Números e passos)
    if re.search(r'\b\d+\b', text) or any(k in txt_lower for k in ("passo 1", "passo 2", "por cento", "%")):
        return {
            "category": "data_stats",
            "label": "Dado & Ensinamento",
            "icon": "fa-chart-pie",
            "badge_class": "bg-primary text-white"
        }

    # 2. Pergunta ou Explicação / Dica
    if "?" in text or any(k in txt_lower for k in ("como ", "por que", "porque", "qual ", "o que", "razão", "explicar", "dica")):
        return {
            "category": "question_explain",
            "label": "Explicação & Dica",
            "icon": "fa-lightbulb",
            "badge_class": "bg-warning text-dark"
        }

    # 3. Gancho de Impacto / Viral
    if any(k in txt_lower for k in ("segredo", "nunca", "erro", "incrível", "atenção", "descubra", "verdade", "resultado", "melhor", "pior", "cuidado")):
        return {
            "category": "viral_hook",
            "label": "Gancho de Impacto",
            "icon": "fa-fire",
            "badge_class": "bg-danger text-white"
        }

    # 4. História / Narrativa
    if any(k in txt_lower for k in ("um dia", "quando eu", "aconteceu", "lembro", "minha experiência", "começou", "passei por")):
        return {
            "category": "storytelling",
            "label": "História & Narrativa",
            "icon": "fa-book-open",
            "badge_class": "bg-info text-dark"
        }

    # 5. Fala & Reflexão padrão
    return {
        "category": "conversation",
        "label": "Fala & Reflexão",
        "icon": "fa-comments",
        "badge_class": "bg-secondary text-white"
    }


def flatten_words(transcription: dict) -> list[dict]:
    words = []
    for segment in transcription.get("segments", []):
        words.extend(segment.get("words", []) or [])
    return words


def _is_boundary(word: dict, next_word: dict | None, pause_threshold: float) -> bool:
    text = word["word"].strip()
    if text.endswith(SENTENCE_END_CHARS):
        return True
    if next_word is None:
        return True
    gap = next_word["start"] - word["end"]
    return gap >= pause_threshold


def find_boundary_indices(words: list[dict], pause_threshold: float = DEFAULT_PAUSE_THRESHOLD) -> set[int]:
    """Indices into `words` that are valid clip end-points."""
    boundaries = set()
    for i, word in enumerate(words):
        next_word = words[i + 1] if i + 1 < len(words) else None
        if _is_boundary(word, next_word, pause_threshold):
            boundaries.add(i)
    return boundaries


def validate_clip_word_alignment(words: list[dict], start: float, end: float) -> bool:
    """True only if `start` and `end` land exactly on a word start/end — i.e.
    the clip never begins or finishes in the middle of a word."""
    starts_on_word = any(abs(w["start"] - start) < 1e-6 for w in words)
    ends_on_word = any(abs(w["end"] - end) < 1e-6 for w in words)
    return starts_on_word and ends_on_word


def generate_candidate_clips(
    transcription: dict,
    min_duration: float,
    max_duration: float,
    pause_threshold: float = DEFAULT_PAUSE_THRESHOLD,
    max_clips: int | None = None,
) -> list[ClipCandidate]:
    """Greedy, non-overlapping segmentation.

    From each valid start point, extends to the furthest boundary whose
    duration still fits within `max_duration`. A run of words with no
    internal boundary that already exceeds `max_duration` is skipped rather
    than force-cut, matching the "não termine cortado" requirement.
    """
    if min_duration <= 0 or max_duration <= 0 or min_duration > max_duration:
        raise ValueError("min_duration must be > 0 and <= max_duration")

    words = flatten_words(transcription)
    if not words:
        return []

    boundary_indices = find_boundary_indices(words, pause_threshold)
    valid_starts = sorted({0} | {b + 1 for b in boundary_indices if b + 1 < len(words)})

    candidates: list[ClipCandidate] = []
    covered_until = -1

    for start_idx in valid_starts:
        if start_idx <= covered_until:
            continue

        best_end_idx = None
        for j in range(start_idx, len(words)):
            if j not in boundary_indices:
                continue
            duration = words[j]["end"] - words[start_idx]["start"]
            if duration > max_duration:
                break
            if duration >= min_duration:
                best_end_idx = j

        if best_end_idx is None:
            continue

        clip_words = words[start_idx:best_end_idx + 1]
        candidates.append(ClipCandidate(
            start=words[start_idx]["start"],
            end=words[best_end_idx]["end"],
            text=" ".join(w["word"].strip() for w in clip_words),
            word_start_index=start_idx,
            word_end_index=best_end_idx,
        ))
        covered_until = best_end_idx

        if max_clips is not None and len(candidates) >= max_clips:
            break

    return candidates


def snap_to_nearest_boundaries(transcription: dict, start: float, end: float,
                                tolerance: float = 2.5) -> dict | None:
    """Snaps a rough, externally-suggested (start, end) to the nearest valid
    word-aligned boundaries actually present in the transcript.

    Used to reconcile an AI provider's approximate timestamps (Fase 3) with
    the deterministic rules from this module: the provider decides *what* is
    worth clipping, this function decides *exactly where* it is safe to cut.
    Returns None if no boundary lies within `tolerance` seconds of the
    requested start or end — the caller should then discard the suggestion
    rather than cut at an unverified timestamp.
    """
    words = flatten_words(transcription)
    if not words:
        return None

    boundary_indices = find_boundary_indices(words)
    valid_starts = sorted({0} | {b + 1 for b in boundary_indices if b + 1 < len(words)})

    def closest_index(indices, target_time: float, time_key: str):
        best_idx = None
        best_diff = None
        for idx in indices:
            diff = abs(words[idx][time_key] - target_time)
            if best_diff is None or diff < best_diff:
                best_diff = diff
                best_idx = idx
        if best_idx is not None and best_diff <= tolerance:
            return best_idx
        return None

    start_idx = closest_index(valid_starts, start, "start")
    if start_idx is None:
        return None

    candidate_ends = [b for b in boundary_indices if b >= start_idx]
    end_idx = closest_index(candidate_ends, end, "end")
    if end_idx is None:
        return None

    return {
        "start": words[start_idx]["start"],
        "end": words[end_idx]["end"],
        "word_start_index": start_idx,
        "word_end_index": end_idx,
    }


def slice_transcription(transcription: dict, start: float, end: float) -> dict:
    """Extracts the portion of a full-video transcription within [start, end]
    and shifts every timestamp to be relative to `start`.

    Used when rendering one suggested clip (Fase 7) out of a transcription
    computed once for the whole video: captions must stay in sync with the
    cut segment, not with the original full-video timeline.
    """
    sliced_segments = []
    for segment in transcription.get("segments", []):
        if segment["end"] <= start or segment["start"] >= end:
            continue

        sliced_words = []
        for word in segment.get("words", []) or []:
            if word["end"] <= start or word["start"] >= end:
                continue
            sliced_words.append({
                **word,
                "start": max(0.0, word["start"] - start),
                "end": max(0.0, word["end"] - start),
            })
        if not sliced_words:
            continue

        sliced_segments.append({
            **segment,
            "start": sliced_words[0]["start"],
            "end": sliced_words[-1]["end"],
            "words": sliced_words,
        })

    return {
        **{k: v for k, v in transcription.items() if k not in ("segments", "duration")},
        "duration": max(0.0, end - start),
        "segments": sliced_segments,
    }
