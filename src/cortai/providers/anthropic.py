"""Anthropic provider: clip-suggestion intelligence using Claude Haiku 5.5 by default."""
import json
import logging
import httpx

from .base import ProviderError, ProviderUnavailable, validate_clip_suggestions_schema
from .prompt import build_suggestion_prompt

logger = logging.getLogger(__name__)

API_URL = "https://api.anthropic.com/v1/messages"
DEFAULT_MODEL = "claude-haiku-5-5"


class AnthropicProvider:
    name = "anthropic"

    def __init__(self, api_key: str | None, model: str = DEFAULT_MODEL,
                 timeout_seconds: float = 60.0):
        self.api_key = api_key
        self.model = model or DEFAULT_MODEL
        self.timeout_seconds = timeout_seconds

    def is_configured(self) -> bool:
        return bool(self.api_key)

    def suggest_clips(self, transcription: dict, min_duration: float, max_duration: float,
                       max_clips: int = 5, language: str = None) -> list[dict]:
        if not self.is_configured():
            raise ProviderUnavailable("ANTHROPIC_API_KEY nao configurada")

        prompt = build_suggestion_prompt(transcription, min_duration, max_duration, max_clips, language)
        headers = {
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
        }
        payload = {
            "model": self.model,
            # Modelos atuais pensam antes de responder e o raciocínio conta dentro de max_tokens.
            "max_tokens": 16000,
            "output_config": {"effort": "low"},
            "system": "Você é um editor especialista de vídeos virais. Responda estritamente com o objeto JSON contendo a chave 'clips'.",
            "messages": [
                {"role": "user", "content": prompt}
            ],
        }

        try:
            response = httpx.post(API_URL, json=payload, headers=headers, timeout=self.timeout_seconds)
            if response.status_code != 200:
                raise ProviderError(f"Anthropic HTTP {response.status_code}: {response.text}")

            data = response.json()
            stop_reason = data.get("stop_reason")
            if stop_reason in ("refusal", "max_tokens"):
                raise ProviderError(f"Anthropic encerrou sem resposta completa (stop_reason={stop_reason})")
            # A resposta pode trazer blocos de raciocínio antes do texto; pega o primeiro bloco de texto.
            content = next(b["text"] for b in data.get("content", []) if b.get("type") == "text")
            # Extract JSON block if wrapped in markdown code fence
            if "```json" in content:
                content = content.split("```json")[1].split("```")[0].strip()
            elif "```" in content:
                content = content.split("```")[1].split("```")[0].strip()

            parsed_json = json.loads(content)
            return validate_clip_suggestions_schema(parsed_json)
        except Exception as exc:
            logger.error("Erro na requisicao Anthropic: %s", exc)
            raise ProviderError(f"Falha no provedor Anthropic: {exc}") from exc
