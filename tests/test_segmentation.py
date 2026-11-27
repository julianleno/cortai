import json
import os
import unittest

from cortai.segmentation import (
    generate_candidate_clips,
    validate_clip_word_alignment,
    flatten_words,
    slice_transcription,
)

FIXTURE_PATH = os.path.join(os.path.dirname(__file__), "fixtures", "flite_sample_transcript.json")


def _load_real_fixture() -> dict:
    """Real transcription produced by ffmpeg's flite TTS + faster-whisper
    (base model), captured once via tests/fixtures/flite_sample_transcript.json.
    Ground truth for proving the algorithm against real (if synthetic-voice)
    speech, not just hand-built word lists.
    """
    with open(FIXTURE_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def _make_transcription(sentences: list[tuple[str, float]], gap: float = 0.1) -> dict:
    """Builds a synthetic transcription dict. Each sentence is a
    (text, duration) pair; words are split on spaces and given equal-length
    slices of `duration`, with `gap` seconds of silence before the next
    sentence."""
    words = []
    cursor = 0.0
    for text, duration in sentences:
        tokens = text.split(" ")
        per_word = duration / len(tokens)
        for token in tokens:
            words.append({"word": f" {token}", "start": cursor, "end": cursor + per_word, "probability": 0.99})
            cursor += per_word
        cursor += gap
    return {"segments": [{"id": 0, "start": words[0]["start"], "end": words[-1]["end"], "text": "", "words": words}]}


class TestSegmentationReal(unittest.TestCase):
    """Uses a real Whisper transcription of a locally synthesized (flite) clip."""

    def setUp(self):
        self.transcription = _load_real_fixture()
        self.words = flatten_words(self.transcription)

    def test_at_least_one_valid_candidate(self):
        candidates = generate_candidate_clips(self.transcription, min_duration=1.0, max_duration=6.0)
        self.assertGreaterEqual(len(candidates), 1)
        for candidate in candidates:
            self.assertTrue(
                validate_clip_word_alignment(self.words, candidate.start, candidate.end),
                f"candidate {candidate} does not align to word boundaries",
            )

    def test_run_exceeding_max_duration_is_skipped_not_force_cut(self):
        # The only internal pause boundary sits at ~4.04s; nothing before it
        # fits under a 3.5s cap, so that whole opening run must be skipped
        # rather than cut mid-sentence.
        candidates = generate_candidate_clips(self.transcription, min_duration=1.0, max_duration=3.5)
        for candidate in candidates:
            self.assertLessEqual(candidate.duration, 3.5)
            self.assertTrue(validate_clip_word_alignment(self.words, candidate.start, candidate.end))
        # None of the candidates may start at the very first word, since the
        # opening run is too long to fit under the cap.
        self.assertTrue(all(c.word_start_index != 0 for c in candidates))

    def test_candidates_never_overlap(self):
        candidates = generate_candidate_clips(self.transcription, min_duration=0.5, max_duration=2.0)
        for a, b in zip(candidates, candidates[1:]):
            self.assertLessEqual(a.word_end_index, b.word_start_index - 1)


class TestSegmentationSynthetic(unittest.TestCase):

    def test_multiple_clean_sentences_produce_multiple_non_overlapping_candidates(self):
        transcription = _make_transcription([
            ("The first sentence ends here.", 2.0),
            ("The second sentence is also short.", 2.0),
            ("A third and final sentence.", 2.0),
        ], gap=0.1)
        words = flatten_words(transcription)

        candidates = generate_candidate_clips(transcription, min_duration=1.0, max_duration=2.5)

        self.assertEqual(len(candidates), 3)
        for candidate in candidates:
            self.assertTrue(validate_clip_word_alignment(words, candidate.start, candidate.end))
            self.assertGreaterEqual(candidate.duration, 1.0)
            self.assertLessEqual(candidate.duration, 2.5)
        # non-overlapping and in order
        for a, b in zip(candidates, candidates[1:]):
            self.assertLess(a.end, b.start)

    def test_sentence_longer_than_max_duration_with_no_internal_boundary_is_dropped(self):
        # One giant "sentence" (no punctuation, no pauses) longer than max_duration.
        transcription = _make_transcription([("word " * 40, 20.0)], gap=0.0)
        candidates = generate_candidate_clips(transcription, min_duration=1.0, max_duration=5.0)
        self.assertEqual(candidates, [])

    def test_empty_transcription_returns_no_candidates(self):
        self.assertEqual(generate_candidate_clips({"segments": []}, 1.0, 5.0), [])

    def test_min_greater_than_max_raises(self):
        transcription = _make_transcription([("Hello world.", 1.0)])
        with self.assertRaises(ValueError):
            generate_candidate_clips(transcription, min_duration=10.0, max_duration=1.0)

    def test_validate_clip_word_alignment_rejects_mid_word_boundaries(self):
        transcription = _make_transcription([("A clean short sentence.", 2.0)])
        words = flatten_words(transcription)
        self.assertFalse(validate_clip_word_alignment(words, words[0]["start"] + 0.01, words[-1]["end"]))
        self.assertFalse(validate_clip_word_alignment(words, words[0]["start"], words[-1]["end"] - 0.01))
        self.assertTrue(validate_clip_word_alignment(words, words[0]["start"], words[-1]["end"]))


class TestSliceTranscription(unittest.TestCase):

    def setUp(self):
        self.transcription = _load_real_fixture()

    def test_slice_shifts_timestamps_relative_to_start(self):
        # Real fixture: boundary at ~4.04s ("words,"), clip ends at 5.6s ("finish.").
        sliced = slice_transcription(self.transcription, start=4.4, end=5.6)
        words = flatten_words(sliced)
        self.assertGreater(len(words), 0)
        self.assertAlmostEqual(words[0]["start"], 0.0, delta=1e-6)
        self.assertAlmostEqual(words[-1]["end"], 1.2, delta=1e-6)

    def test_slice_excludes_words_outside_range(self):
        full_words = flatten_words(self.transcription)
        sliced = slice_transcription(self.transcription, start=4.4, end=5.6)
        sliced_words = flatten_words(sliced)
        self.assertLess(len(sliced_words), len(full_words))

    def test_slice_of_full_range_keeps_all_words(self):
        full_words = flatten_words(self.transcription)
        sliced = slice_transcription(self.transcription, start=0.0, end=6.0)
        sliced_words = flatten_words(sliced)
        self.assertEqual(len(sliced_words), len(full_words))

    def test_slice_outside_any_content_returns_no_segments(self):
        sliced = slice_transcription(self.transcription, start=100.0, end=110.0)
        self.assertEqual(sliced["segments"], [])

    def test_slice_preserves_other_transcription_fields(self):
        sliced = slice_transcription(self.transcription, start=0.0, end=6.0)
        self.assertEqual(sliced["language"], self.transcription["language"])


if __name__ == "__main__":
    unittest.main()
