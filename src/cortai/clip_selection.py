"""Orquestrador de Seleção de Clipes por IA (Fase 3):
Gemini -> OpenAI -> Anthropic -> NVIDIA NIM -> Regras Locais Determinísticas.

Se NENHUMA chave de API estiver configurada, cai automaticamente para as
regras locais (segmentation.py) sem realizar chamadas externas.
"""
import logging
import os
from .providers.base import ProviderError
from .providers.gemini import GeminiProvider
from .providers.openai import OpenAIProvider
from .providers.anthropic import AnthropicProvider
from .providers.nvidia_nim import NvidiaNimProvider
from .segmentation import generate_candidate_clips, snap_to_nearest_boundaries, categorize_local_text
from . import db
from .config import config

logger = logging.getLogger(__name__)

MIN_LOCAL_CLIP_SCORE = 5.0


def local_candidate_to_suggestion(candidate) -> dict:
    words_preview = candidate.text.split()
    title_text = " ".join(words_preview[:6]).strip(".,!?;:")
    title = f"Clipe: {title_text.capitalize()}" if title_text else "Clipe sem título"
    hook = " ".join(words_preview[:12])
    cat = categorize_local_text(candidate.text)

    return {
        "start": candidate.start,
        "end": candidate.end,
        "title": title,
        "hook": hook,
        "justificativa": f"[{cat['label']}] Selecionado por regra local determinística.",
        "clip_score": MIN_LOCAL_CLIP_SCORE,
        "source": "local",
        "category": cat["category"],
        "category_label": cat["label"],
        "category_icon": cat["icon"],
        "category_badge_class": cat["badge_class"],
    }


def reconcile_suggestions(suggestions: list[dict], transcription: dict,
                           min_duration: float, max_duration: float,
                           tolerance: float = 2.5) -> list[dict]:
    """Snaps each suggestion to real word boundaries, drops anything that
    doesn't land near a valid boundary or falls outside the requested
    duration range after snapping, and removes overlapping duplicates
    (keeping the higher-scored clip)."""
    used_ranges: list[tuple[int, int]] = []
    reconciled = []

    for suggestion in sorted(suggestions, key=lambda s: -s["clip_score"]):
        snapped = snap_to_nearest_boundaries(transcription, suggestion["start"], suggestion["end"], tolerance)
        if snapped is None:
            logger.info("Descartando sugestao sem fronteira valida proxima: %s", suggestion)
            continue

        duration = snapped["end"] - snapped["start"]
        if duration < min_duration or duration > max_duration:
            logger.info("Descartando sugestao fora da duracao [%s, %s] apos snap: %.2fs",
                        min_duration, max_duration, duration)
            continue

        overlaps = any(
            not (snapped["word_end_index"] < used_start or snapped["word_start_index"] > used_end)
            for used_start, used_end in used_ranges
        )
        if overlaps:
            continue

        used_ranges.append((snapped["word_start_index"], snapped["word_end_index"]))
        reconciled.append({
            **suggestion,
            "start": snapped["start"],
            "end": snapped["end"],
        })

    reconciled.sort(key=lambda c: c["start"])
    return reconciled


def suggest_clips(transcription: dict, min_duration: float, max_duration: float,
                    max_clips: int = 5, language: str = None,
                    gemini_api_key: str = None, nvidia_api_key: str = None,
                    openai_api_key: str = None, anthropic_api_key: str = None,
                    gemini_model: str = None, nvidia_model: str = None,
                    openai_model: str = None, anthropic_model: str = None) -> list[dict]:
    """Orquestra a busca pelos melhores trechos.
    Verifica se há chaves salvas no banco de dados SQLite (tabela settings)
    ou nas variáveis de ambiente. Se NENHUMA chave for configurada, roda 100% local!"""

    db_path = config.DB_PATH

    # Load API keys from settings DB first, falling back to arguments/env
    gemini_key = db.get_setting(db_path, "gemini_api_key", gemini_api_key or config.GEMINI_API_KEY)
    openai_key = db.get_setting(db_path, "openai_api_key", openai_api_key or os.getenv("OPENAI_API_KEY"))
    anthropic_key = db.get_setting(db_path, "anthropic_api_key", anthropic_api_key or os.getenv("ANTHROPIC_API_KEY"))
    nvidia_key = db.get_setting(db_path, "nvidia_nim_api_key", nvidia_api_key or config.NVIDIA_NIM_API_KEY)

    gemini_m = db.get_setting(db_path, "gemini_model", gemini_model or config.GEMINI_MODEL)
    openai_m = db.get_setting(db_path, "openai_model", openai_model or "gpt-4o-mini")
    anthropic_m = db.get_setting(db_path, "anthropic_model", anthropic_model or "claude-haiku-5-5")
    nvidia_m = db.get_setting(db_path, "nvidia_nim_model", nvidia_model or config.NVIDIA_NIM_MODEL)

    providers = []
    if gemini_key:
        providers.append(GeminiProvider(gemini_key, model=gemini_m))
    if openai_key:
        providers.append(OpenAIProvider(openai_key, model=openai_m))
    if anthropic_key:
        providers.append(AnthropicProvider(anthropic_key, model=anthropic_m))
    if nvidia_key:
        providers.append(NvidiaNimProvider(nvidia_key, model=nvidia_m))

    for provider in providers:
        try:
            logger.info("Tentando selecao com provedor IA: %s", provider.name)
            raw_suggestions = provider.suggest_clips(transcription, min_duration, max_duration, max_clips, language)
            reconciled = reconcile_suggestions(raw_suggestions, transcription, min_duration, max_duration)
            if reconciled:
                for clip in reconciled:
                    clip["source"] = provider.name
                return reconciled[:max_clips]
            logger.warning("Provedor %s respondeu mas nenhuma sugestao passou na reconciliacao", provider.name)
        except ProviderError as exc:
            logger.warning("Provedor %s falhou: %s", provider.name, exc)
            continue

    logger.info("Nenhuma chave de API configurada/disponivel — executando 100% OFFLINE / LOCAL")
    local_candidates = generate_candidate_clips(transcription, min_duration, max_duration, max_clips=max_clips)
    return [local_candidate_to_suggestion(c) for c in local_candidates]

# Alias for backwards compatibility
suggest_clips_for_video = suggest_clips
