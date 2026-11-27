"""Anthropic provider: clip-suggestion intelligence using Claude 3.5 Sonnet / Claude 3 Haiku."""
import json
import logging
import httpx

from .base import ProviderError, ProviderUnavailable, validate_clip_suggestions_schema
from .prompt import build_suggestion_prompt

logger = logging.getLogger(__name__)

API_URL = "https://api.anthropic.com/v1/messages"
DEFAULT_MODEL = "claude-3-5-haiku-20241022"


class AnthropicProvider:
    name = "anthropic"

    def __init__(self, api_key: str | None, model: str = DEFAULT_MODEL,
                 timeout_seconds: float = 35.0):
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
            "max_tokens": 2048,
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
            content = data["content"][0]["text"]
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
