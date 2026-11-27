"""Gemini provider: primary clip-suggestion intelligence (free tier)."""
import json
import logging
import time

import httpx

from .base import ProviderResponseInvalid, ProviderUnavailable, validate_clip_suggestions_schema
from .prompt import CLIP_SUGGESTION_JSON_SCHEMA, build_suggestion_prompt

logger = logging.getLogger(__name__)

API_BASE = "https://generativelanguage.googleapis.com/v1beta/models"
DEFAULT_MODEL = "gemini-flash-latest"
RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}


class GeminiProvider:
    name = "gemini"

    def __init__(self, api_key: str | None, model: str = DEFAULT_MODEL,
                 max_retries: int = 2, retry_backoff_seconds: float = 1.0,
                 timeout_seconds: float = 30.0):
        self.api_key = api_key
        self.model = model
        self.max_retries = max_retries
        self.retry_backoff_seconds = retry_backoff_seconds
        self.timeout_seconds = timeout_seconds

    def is_configured(self) -> bool:
        return bool(self.api_key)

    def suggest_clips(self, transcription: dict, min_duration: float, max_duration: float,
                       max_clips: int = 5, language: str = None) -> list[dict]:
        if not self.is_configured():
            raise ProviderUnavailable("GEMINI_API_KEY nao configurada")

        prompt = build_suggestion_prompt(transcription, min_duration, max_duration, max_clips, language)
        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                "responseMimeType": "application/json",
                "responseSchema": CLIP_SUGGESTION_JSON_SCHEMA,
            },
        }

        last_error = None
        for attempt in range(1, self.max_retries + 2):
            try:
                response = httpx.post(
                    f"{API_BASE}/{self.model}:generateContent",
                    params={"key": self.api_key},
                    json=payload,
                    timeout=self.timeout_seconds,
                )
            except httpx.HTTPError as exc:
                last_error = ProviderUnavailable(f"Falha de rede ao chamar Gemini: {exc}")
                logger.warning("Gemini attempt %d/%d failed: %s", attempt, self.max_retries + 1, exc)
                self._sleep_before_retry(attempt)
                continue

            if response.status_code == 200:
                try:
                    body = response.json()
                    text = body["candidates"][0]["content"]["parts"][0]["text"]
                    parsed = json.loads(text)
                    return validate_clip_suggestions_schema(parsed)
                except (KeyError, IndexError, json.JSONDecodeError, ProviderResponseInvalid) as exc:
                    raise ProviderResponseInvalid(f"Resposta do Gemini fora do schema esperado: {exc}") from exc

            if response.status_code in RETRYABLE_STATUS_CODES:
                last_error = ProviderUnavailable(
                    f"Gemini respondeu {response.status_code}: {response.text[:200]}"
                )
                logger.warning("Gemini attempt %d/%d got status %d, retrying",
                                attempt, self.max_retries + 1, response.status_code)
                self._sleep_before_retry(attempt)
                continue

            raise ProviderUnavailable(f"Gemini respondeu {response.status_code}: {response.text[:200]}")

        raise last_error or ProviderUnavailable("Gemini indisponivel apos retries")

    def _sleep_before_retry(self, attempt: int) -> None:
        if attempt <= self.max_retries and self.retry_backoff_seconds > 0:
            time.sleep(self.retry_backoff_seconds)
