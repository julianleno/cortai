"""Provider-agnostic contract for clip-suggestion intelligence (Fase 3).

CLAUDE.md requires the AI layer to be a swappable abstraction that never
gets treated as an operational result until its response passes schema
validation, and that failures degrade to a controlled fallback instead of
crashing the job.
"""


class ProviderError(Exception):
    """Base class for all provider failures (network, auth, invalid response)."""


class ProviderUnavailable(ProviderError):
    """Provider could not be reached or is not configured (e.g. missing API key)."""


class ProviderResponseInvalid(ProviderError):
    """Provider responded, but the payload failed schema validation."""


REQUIRED_CLIP_FIELDS = ("start", "end", "title", "hook", "justificativa", "clip_score")


def validate_clip_suggestions_schema(payload: dict) -> list[dict]:
    """Validates the provider-agnostic clip-suggestion schema.

    Expected shape: {"clips": [{"start": number, "end": number, "title": str,
    "hook": str, "justificativa": str, "clip_score": number}, ...]}

    Never raises on a malformed individual item — that item is dropped and
    counted as invalid, since one bad suggestion must not fail the whole
    provider response. Raises ProviderResponseInvalid only if the payload
    has no usable structure at all (e.g. not a dict, or no valid clips).
    """
    if not isinstance(payload, dict) or "clips" not in payload:
        raise ProviderResponseInvalid(f"Resposta sem chave 'clips': {payload!r}")

    raw_clips = payload["clips"]
    if not isinstance(raw_clips, list):
        raise ProviderResponseInvalid(f"'clips' nao e uma lista: {raw_clips!r}")

    valid_clips = []
    for item in raw_clips:
        if not isinstance(item, dict):
            continue
        if not all(field in item for field in REQUIRED_CLIP_FIELDS):
            continue
        try:
            start = float(item["start"])
            end = float(item["end"])
            clip_score = float(item["clip_score"])
        except (TypeError, ValueError):
            continue
        if start < 0 or end <= start:
            continue
        if not (0 <= clip_score <= 10):
            continue
        if not isinstance(item["title"], str) or not isinstance(item["hook"], str) \
                or not isinstance(item["justificativa"], str):
            continue

        valid_clips.append({
            "start": start,
            "end": end,
            "title": item["title"].strip(),
            "hook": item["hook"].strip(),
            "justificativa": item["justificativa"].strip(),
            "clip_score": clip_score,
        })

    if not valid_clips:
        raise ProviderResponseInvalid("Nenhum clipe valido na resposta apos validacao de schema")

    return valid_clips
