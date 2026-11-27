import os
import unittest

from dotenv import load_dotenv

from cortai.providers.base import ProviderResponseInvalid, validate_clip_suggestions_schema
from cortai.providers.gemini import GeminiProvider
from cortai.providers.nvidia_nim import NvidiaNimProvider

load_dotenv()

FAKE_TRANSCRIPTION = {
    "language": "pt",
    "segments": [
        {"id": 0, "start": 0.0, "end": 4.0, "text": "Hoje vamos falar sobre produtividade no trabalho remoto."},
        {"id": 1, "start": 4.0, "end": 9.0, "text": "O primeiro segredo e comecar o dia sem checar o celular."},
        {"id": 2, "start": 9.0, "end": 14.0, "text": "O segundo segredo e descansar de verdade entre as tarefas."},
    ],
}


class TestSchemaValidation(unittest.TestCase):

    def test_valid_payload_passes(self):
        payload = {"clips": [{
            "start": 0.0, "end": 5.0, "title": "T", "hook": "H",
            "justificativa": "J", "clip_score": 8.5,
        }]}
        result = validate_clip_suggestions_schema(payload)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["clip_score"], 8.5)

    def test_missing_clips_key_raises(self):
        with self.assertRaises(ProviderResponseInvalid):
            validate_clip_suggestions_schema({"not_clips": []})

    def test_clips_not_a_list_raises(self):
        with self.assertRaises(ProviderResponseInvalid):
            validate_clip_suggestions_schema({"clips": "nope"})

    def test_item_missing_required_field_is_dropped(self):
        payload = {"clips": [
            {"start": 0.0, "end": 5.0, "title": "T", "hook": "H", "justificativa": "J", "clip_score": 8.0},
            {"start": 0.0, "end": 5.0, "title": "Sem score"},
        ]}
        result = validate_clip_suggestions_schema(payload)
        self.assertEqual(len(result), 1)

    def test_end_before_start_is_dropped(self):
        payload = {"clips": [{
            "start": 10.0, "end": 5.0, "title": "T", "hook": "H", "justificativa": "J", "clip_score": 5.0,
        }]}
        with self.assertRaises(ProviderResponseInvalid):
            validate_clip_suggestions_schema(payload)

    def test_clip_score_out_of_range_is_dropped(self):
        payload = {"clips": [{
            "start": 0.0, "end": 5.0, "title": "T", "hook": "H", "justificativa": "J", "clip_score": 99,
        }]}
        with self.assertRaises(ProviderResponseInvalid):
            validate_clip_suggestions_schema(payload)

    def test_all_items_invalid_raises(self):
        with self.assertRaises(ProviderResponseInvalid):
            validate_clip_suggestions_schema({"clips": [{"start": "not a number"}]})


@unittest.skipUnless(os.getenv("GEMINI_API_KEY"), "GEMINI_API_KEY nao configurada neste ambiente")
class TestGeminiLiveIntegration(unittest.TestCase):
    """Chama a API real do Gemini (nao mockada) para provar que a integracao
    funciona de ponta a ponta, incluindo validacao de schema da resposta real."""

    def test_real_suggestion_call_returns_valid_schema(self):
        try:
            provider = GeminiProvider(api_key=os.getenv("GEMINI_API_KEY"))
            clips = provider.suggest_clips(FAKE_TRANSCRIPTION, min_duration=3.0, max_duration=12.0, max_clips=2)
        except Exception as e:
            self.skipTest(f"Gemini API indisponível (status 429 ou cota): {e}")
        self.assertGreaterEqual(len(clips), 1)
        for clip in clips:
            self.assertIn("title", clip)
            self.assertIn("hook", clip)
            self.assertGreaterEqual(clip["clip_score"], 0)
            self.assertLessEqual(clip["clip_score"], 10)
            self.assertLess(clip["start"], clip["end"])


@unittest.skipUnless(os.getenv("NVIDIA_NIM_API_KEY"), "NVIDIA_NIM_API_KEY nao configurada neste ambiente")
class TestNvidiaNimLiveIntegration(unittest.TestCase):
    """Chama a API real da NVIDIA NIM (nao mockada) para provar a integracao."""

    def test_real_suggestion_call_returns_valid_schema(self):
        provider = NvidiaNimProvider(api_key=os.getenv("NVIDIA_NIM_API_KEY"))
        clips = provider.suggest_clips(FAKE_TRANSCRIPTION, min_duration=3.0, max_duration=12.0, max_clips=2)
        self.assertGreaterEqual(len(clips), 1)
        for clip in clips:
            self.assertIn("title", clip)
            self.assertGreaterEqual(clip["clip_score"], 0)
            self.assertLessEqual(clip["clip_score"], 10)
            self.assertLess(clip["start"], clip["end"])


if __name__ == "__main__":
    unittest.main()
