"""OpenAI provider: clip-suggestion intelligence using GPT-4o / GPT-4o-mini."""
import json
import logging
import httpx

from .base import ProviderError, ProviderUnavailable, validate_clip_suggestions_schema
from .prompt import build_suggestion_prompt

logger = logging.getLogger(__name__)

API_URL = "https://api.openai.com/v1/chat/completions"
DEFAULT_MODEL = "gpt-4o-mini"


class OpenAIProvider:
    name = "openai"

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
            raise ProviderUnavailable("OPENAI_API_KEY nao configurada")

        prompt = build_suggestion_prompt(transcription, min_duration, max_duration, max_clips, language)
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": "Você é um editor profissional de vídeos virais para redes sociais. Responda estritamente em formato JSON."},
                {"role": "user", "content": prompt}
            ],
            "response_format": {"type": "json_object"},
            "temperature": 0.3,
        }

        try:
            response = httpx.post(API_URL, json=payload, headers=headers, timeout=self.timeout_seconds)
            if response.status_code != 200:
                raise ProviderError(f"OpenAI HTTP {response.status_code}: {response.text}")

            data = response.json()
            content = data["choices"][0]["message"]["content"]
            parsed_json = json.loads(content)
            return validate_clip_suggestions_schema(parsed_json)
        except Exception as exc:
            logger.error("Erro na requisicao OpenAI: %s", exc)
            raise ProviderError(f"Falha no provedor OpenAI: {exc}") from exc
