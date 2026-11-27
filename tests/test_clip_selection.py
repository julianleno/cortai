import json
import os
import unittest
from unittest import mock

from cortai import clip_selection
from cortai.providers.base import ProviderUnavailable
from cortai.providers.gemini import GeminiProvider
from cortai.providers.nvidia_nim import NvidiaNimProvider
from cortai.config import config

FIXTURE_PATH = os.path.join(os.path.dirname(__file__), "fixtures", "flite_sample_transcript.json")


def _load_real_fixture() -> dict:
    with open(FIXTURE_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


class TestReconcileSuggestions(unittest.TestCase):

    def setUp(self):
        self.transcription = _load_real_fixture()

    def test_suggestion_snapped_to_real_word_boundaries(self):
        # Rough LLM-style guess close to the whole clip (real fixture spans 0.0-5.6s).
        suggestions = [{
            "start": 0.2, "end": 5.3, "title": "T", "hook": "H",
            "justificativa": "J", "clip_score": 8.0,
        }]
        reconciled = clip_selection.reconcile_suggestions(
            suggestions, self.transcription, min_duration=1.0, max_duration=6.0,
        )
        self.assertEqual(len(reconciled), 1)
        self.assertAlmostEqual(reconciled[0]["start"], 0.0)
        self.assertAlmostEqual(reconciled[0]["end"], 5.6)

    def test_suggestion_far_from_any_boundary_is_dropped(self):
        suggestions = [{
            "start": 100.0, "end": 120.0, "title": "T", "hook": "H",
            "justificativa": "J", "clip_score": 8.0,
        }]
        reconciled = clip_selection.reconcile_suggestions(
            suggestions, self.transcription, min_duration=1.0, max_duration=6.0,
        )
        self.assertEqual(reconciled, [])

    def test_overlapping_suggestions_keep_only_higher_score(self):
        suggestions = [
            {"start": 0.2, "end": 5.3, "title": "Baixo score", "hook": "H",
             "justificativa": "J", "clip_score": 3.0},
            {"start": 0.1, "end": 5.5, "title": "Alto score", "hook": "H",
             "justificativa": "J", "clip_score": 9.0},
        ]
        reconciled = clip_selection.reconcile_suggestions(
            suggestions, self.transcription, min_duration=1.0, max_duration=6.0,
        )
        self.assertEqual(len(reconciled), 1)
        self.assertEqual(reconciled[0]["title"], "Alto score")

    def test_snapped_duration_outside_range_is_dropped(self):
        # Snaps to the 0.0-5.6s full-clip boundary, which exceeds a 2s cap.
        suggestions = [{
            "start": 0.2, "end": 5.3, "title": "T", "hook": "H",
            "justificativa": "J", "clip_score": 8.0,
        }]
        reconciled = clip_selection.reconcile_suggestions(
            suggestions, self.transcription, min_duration=0.5, max_duration=2.0,
        )
        self.assertEqual(reconciled, [])


class TestSuggestClipsFallbackChain(unittest.TestCase):

    def setUp(self):
        self.transcription = _load_real_fixture()

    def test_no_keys_configured_goes_straight_to_local(self):
        with mock.patch.object(GeminiProvider, "suggest_clips") as gemini_mock, \
             mock.patch.object(NvidiaNimProvider, "suggest_clips") as nvidia_mock, \
             mock.patch.object(config, "GEMINI_API_KEY", None), \
             mock.patch.object(config, "NVIDIA_NIM_API_KEY", None):
            result = clip_selection.suggest_clips(
                self.transcription, min_duration=1.0, max_duration=6.0,
                gemini_api_key=None, nvidia_api_key=None,
            )
        gemini_mock.assert_not_called()
        nvidia_mock.assert_not_called()
        self.assertTrue(all(c["source"] == "local" for c in result))
        self.assertGreaterEqual(len(result), 1)

    def test_gemini_success_short_circuits_nvidia(self):
        fake_clips = [{"start": 0.2, "end": 5.3, "title": "T", "hook": "H",
                        "justificativa": "J", "clip_score": 8.0}]
        with mock.patch.object(GeminiProvider, "suggest_clips", return_value=fake_clips) as gemini_mock, \
             mock.patch.object(NvidiaNimProvider, "suggest_clips") as nvidia_mock:
            result = clip_selection.suggest_clips(
                self.transcription, min_duration=1.0, max_duration=6.0,
                gemini_api_key="fake-gemini-key", nvidia_api_key="fake-nvidia-key",
            )
        gemini_mock.assert_called_once()
        nvidia_mock.assert_not_called()
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["source"], "gemini")

    def test_gemini_failure_falls_back_to_nvidia(self):
        fake_clips = [{"start": 0.2, "end": 5.3, "title": "T", "hook": "H",
                        "justificativa": "J", "clip_score": 8.0}]
        with mock.patch.object(GeminiProvider, "suggest_clips", side_effect=ProviderUnavailable("boom")), \
             mock.patch.object(NvidiaNimProvider, "suggest_clips", return_value=fake_clips) as nvidia_mock:
            result = clip_selection.suggest_clips(
                self.transcription, min_duration=1.0, max_duration=6.0,
                gemini_api_key="fake-gemini-key", nvidia_api_key="fake-nvidia-key",
            )
        nvidia_mock.assert_called_once()
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["source"], "nvidia_nim")

    def test_both_providers_failing_falls_back_to_local(self):
        with mock.patch.object(GeminiProvider, "suggest_clips", side_effect=ProviderUnavailable("boom")), \
             mock.patch.object(NvidiaNimProvider, "suggest_clips", side_effect=ProviderUnavailable("boom too")):
            result = clip_selection.suggest_clips(
                self.transcription, min_duration=1.0, max_duration=6.0,
                gemini_api_key="fake-gemini-key", nvidia_api_key="fake-nvidia-key",
            )
        self.assertTrue(all(c["source"] == "local" for c in result))
        self.assertGreaterEqual(len(result), 1)


if __name__ == "__main__":
    unittest.main()
