"""Correcao de vocabulario para transcricao (siglas, nomes proprios).

Whisper frequentemente erra a grafia de siglas e nomes proprios que nao
conhece foneticamente (ex: "ANAC" -> "ANAK", "VOEPASS" -> "VOEPAZ"). Duas
camadas de correcao, aplicadas nessa ordem, sempre em cima da transcricao
JA em cache (o cache continua vinculado so ao fingerprint do video --
corrigir depois de ler o cache evita ter que invalidar/rechavear o cache
toda vez que o vocabulario mudar):

1. `apply_vocabulary_corrections`: correcao automatica e reutilizavel --
   troca qualquer palavra foneticamente parecida com um termo de um
   glossario (ex: cadastrado num template ou digitado ao enviar o video)
   pela grafia correta, usando `difflib` (sem dependencia nova).
2. `apply_word_overrides`: edicao manual explicita, palavra por palavra,
   feita pelo usuario numa tela de revisao antes de renderizar -- sempre
   tem prioridade sobre a correcao automatica.

Ambas retornam uma NOVA transcricao (copia), nunca mutam a original.
"""
import copy
import difflib
import re

DEFAULT_SIMILARITY_THRESHOLD = 0.65
MIN_WORD_LENGTH_FOR_CORRECTION = 3


def parse_vocabulary_input(raw: str) -> list[str]:
    """Parses a comma- or newline-separated string of terms into a clean,
    deduplicated list, preserving the user's original casing (e.g. 'ANAC',
    'VOEPASS') since that's the correct spelling to substitute in."""
    if not raw:
        return []
    parts = re.split(r"[,\n]", raw)
    seen = set()
    terms = []
    for part in parts:
        term = part.strip()
        if not term or term.lower() in seen:
            continue
        seen.add(term.lower())
        terms.append(term)
    return terms


def word_core(word_text: str) -> str:
    """The editable part of a transcript word token, stripped of leading
    whitespace and trailing punctuation (e.g. ' ANAK,' -> 'ANAK') -- what a
    manual-edit UI should display/submit, since punctuation/whitespace is
    reattached automatically by apply_word_overrides."""
    return _split_word_punctuation(word_text)[1]


def _split_word_punctuation(word_text: str) -> tuple[str, str, str]:
    """Splits a transcript word token into (leading_whitespace, core, trailing_punctuation).
    faster-whisper tokens typically look like ' ANAK,' -- a leading space
    and possibly trailing punctuation around the actual word."""
    match = re.match(r"^(\s*)(.*?)([.,!?;:]*)$", word_text, re.DOTALL)
    if not match:
        return "", word_text, ""
    return match.group(1), match.group(2), match.group(3)


def _best_vocabulary_match(core: str, vocabulary: list[str], threshold: float) -> str | None:
    if len(core) < MIN_WORD_LENGTH_FOR_CORRECTION:
        return None
    best_term = None
    best_score = threshold
    for term in vocabulary:
        if core.lower() == term.lower():
            return None  # already correct, nothing to change
        score = difflib.SequenceMatcher(None, core.lower(), term.lower()).ratio()
        if score > best_score:
            best_score = score
            best_term = term
    return best_term


def apply_vocabulary_corrections(transcription: dict, vocabulary: list[str],
                                  threshold: float = DEFAULT_SIMILARITY_THRESHOLD) -> dict:
    """Returns a corrected copy of `transcription`: any word similar enough
    to a vocabulary term (and not already an exact match) is replaced with
    that term's exact spelling, preserving surrounding whitespace/punctuation."""
    if not vocabulary:
        return transcription

    result = copy.deepcopy(transcription)
    for segment in result.get("segments", []):
        words = segment.get("words") or []
        segment_changed = False
        for word in words:
            leading, core, trailing = _split_word_punctuation(word["word"])
            match = _best_vocabulary_match(core, vocabulary, threshold)
            if match:
                word["word"] = f"{leading}{match}{trailing}"
                segment_changed = True
        if segment_changed:
            segment["text"] = "".join(w["word"] for w in words)
    return result


def apply_word_overrides(transcription: dict, overrides: dict) -> dict:
    """Returns a copy of `transcription` with specific words replaced by
    text, addressed by their index in the flattened word list (the same
    order `segmentation.flatten_words` produces). Overrides always apply
    last -- after vocabulary auto-correction -- since they're an explicit
    manual decision.
    """
    if not overrides:
        return transcription

    overrides = {int(k): v for k, v in overrides.items()}
    result = copy.deepcopy(transcription)
    index = 0
    for segment in result.get("segments", []):
        words = segment.get("words") or []
        segment_changed = False
        for word in words:
            if index in overrides:
                leading, _core, trailing = _split_word_punctuation(word["word"])
                new_core = overrides[index].strip()
                word["word"] = f"{leading}{new_core}{trailing}"
                segment_changed = True
            index += 1
        if segment_changed:
            segment["text"] = "".join(w["word"] for w in words)
    return result


def apply_corrections(transcription: dict, vocabulary: list[str] = None,
                       word_overrides: dict = None) -> dict:
    """Convenience wrapper: vocabulary auto-correction first, then manual
    word overrides (which always win)."""
    corrected = apply_vocabulary_corrections(transcription, vocabulary or [])
    corrected = apply_word_overrides(corrected, word_overrides or {})
    return corrected
