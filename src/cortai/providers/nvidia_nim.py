"""NVIDIA NIM provider: secondary fallback before the local-only path."""
import json
import logging
import re
import time

import httpx

from .base import ProviderResponseInvalid, ProviderUnavailable, validate_clip_suggestions_schema
from .prompt import build_suggestion_prompt

logger = logging.getLogger(__name__)

API_URL = "https://integrate.api.nvidia.com/v1/chat/completions"
DEFAULT_MODEL = "meta/llama-3.1-8b-instruct"
RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}
_JSON_BLOCK_RE = re.compile(r"\{.*\}", re.DOTALL)


class NvidiaNimProvider:
    name = "nvidia_nim"

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
            raise ProviderUnavailable("NVIDIA_NIM_API_KEY nao configurada")

        prompt = build_suggestion_prompt(transcription, min_duration, max_duration, max_clips, language)
        payload = {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.2,
            "max_tokens": 2000,
        }

        last_error = None
        for attempt in range(1, self.max_retries + 2):
            try:
                response = httpx.post(
                    API_URL,
                    headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
                    json=payload,
                    timeout=self.timeout_seconds,
                )
            except httpx.HTTPError as exc:
                last_error = ProviderUnavailable(f"Falha de rede ao chamar NVIDIA NIM: {exc}")
                logger.warning("NVIDIA NIM attempt %d/%d failed: %s", attempt, self.max_retries + 1, exc)
                self._sleep_before_retry(attempt)
                continue

            if response.status_code == 200:
                try:
                    body = response.json()
                    content = body["choices"][0]["message"]["content"]
                    parsed = self._extract_json(content)
                    # Chat models (unlike Gemini's constrained responseSchema
                    # mode) sometimes return a bare array instead of the
                    # requested {"clips": [...]} wrapper.
                    if isinstance(parsed, list):
                        parsed = {"clips": parsed}
                    return validate_clip_suggestions_schema(parsed)
                except (KeyError, IndexError, json.JSONDecodeError, ProviderResponseInvalid) as exc:
                    raise ProviderResponseInvalid(f"Resposta do NVIDIA NIM fora do schema esperado: {exc}") from exc

            if response.status_code in RETRYABLE_STATUS_CODES:
                last_error = ProviderUnavailable(
                    f"NVIDIA NIM respondeu {response.status_code}: {response.text[:200]}"
                )
                logger.warning("NVIDIA NIM attempt %d/%d got status %d, retrying",
                                attempt, self.max_retries + 1, response.status_code)
                self._sleep_before_retry(attempt)
                continue

            raise ProviderUnavailable(f"NVIDIA NIM respondeu {response.status_code}: {response.text[:200]}")

        raise last_error or ProviderUnavailable("NVIDIA NIM indisponivel apos retries")

    @staticmethod
    def _extract_json(content: str) -> dict:
        """Chat models often wrap JSON in prose or markdown fences; unlike
        Gemini's responseSchema mode, there's no guarantee of a raw JSON body."""
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            pass
        match = _JSON_BLOCK_RE.search(content)
        if not match:
            raise ProviderResponseInvalid(f"Nao foi possivel extrair JSON da resposta: {content[:200]}")
        return json.loads(match.group(0))

    def _sleep_before_retry(self, attempt: int) -> None:
        if attempt <= self.max_retries and self.retry_backoff_seconds > 0:
            time.sleep(self.retry_backoff_seconds)
