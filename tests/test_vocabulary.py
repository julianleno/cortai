import unittest

from cortai.vocabulary import (
    apply_corrections,
    apply_vocabulary_corrections,
    apply_word_overrides,
    parse_vocabulary_input,
    word_core,
)


def _transcription(word_specs):
    """word_specs: list of raw word tokens as faster-whisper would emit them,
    e.g. ' ANAK,' (leading space, trailing comma)."""
    words = [{"word": w, "start": i * 0.5, "end": i * 0.5 + 0.4, "probability": 0.9}
              for i, w in enumerate(word_specs)]
    text = "".join(word_specs)
    return {"segments": [{"id": 0, "start": 0.0, "end": len(words) * 0.5, "text": text, "words": words}]}


class TestParseVocabularyInput(unittest.TestCase):

    def test_comma_separated(self):
        self.assertEqual(parse_vocabulary_input("ANAC, VOEPASS"), ["ANAC", "VOEPASS"])

    def test_newline_separated(self):
        self.assertEqual(parse_vocabulary_input("ANAC\nVOEPASS\n"), ["ANAC", "VOEPASS"])

    def test_dedupes_case_insensitively_keeping_first_casing(self):
        self.assertEqual(parse_vocabulary_input("ANAC, anac, ANAC"), ["ANAC"])

    def test_empty_and_whitespace_only_entries_dropped(self):
        self.assertEqual(parse_vocabulary_input("ANAC,, , VOEPASS"), ["ANAC", "VOEPASS"])

    def test_empty_input_returns_empty_list(self):
        self.assertEqual(parse_vocabulary_input(""), [])
        self.assertEqual(parse_vocabulary_input(None), [])


class TestWordCore(unittest.TestCase):

    def test_strips_leading_space_and_trailing_punctuation(self):
        self.assertEqual(word_core(" ANAK,"), "ANAK")

    def test_no_punctuation(self):
        self.assertEqual(word_core(" hello"), "hello")

    def test_multiple_trailing_punctuation(self):
        self.assertEqual(word_core(" wait..."), "wait")


class TestApplyVocabularyCorrections(unittest.TestCase):

    def test_corrects_misheard_acronym_preserving_punctuation(self):
        transcription = _transcription([" a", " ANAK,", " disse."])
        corrected = apply_vocabulary_corrections(transcription, ["ANAC", "VOEPASS"])
        words = corrected["segments"][0]["words"]
        self.assertEqual(words[1]["word"], " ANAC,")
        # Other words untouched.
        self.assertEqual(words[0]["word"], " a")
        self.assertEqual(words[2]["word"], " disse.")

    def test_corrects_multiple_distinct_terms(self):
        transcription = _transcription([" a", " VOEPAZ", " e", " a", " ANAK", " combinaram."])
        corrected = apply_vocabulary_corrections(transcription, ["ANAC", "VOEPASS"])
        words = corrected["segments"][0]["words"]
        self.assertEqual(words[1]["word"], " VOEPASS")
        self.assertEqual(words[4]["word"], " ANAC")

    def test_does_not_touch_already_correct_words(self):
        transcription = _transcription([" a", " ANAC", " confirmou."])
        corrected = apply_vocabulary_corrections(transcription, ["ANAC"])
        self.assertEqual(corrected["segments"][0]["words"][1]["word"], " ANAC")

    def test_does_not_touch_unrelated_short_words(self):
        # "a"/"e" etc. must never get corrected into an unrelated vocabulary
        # term just because of a coincidentally high ratio on tiny strings.
        transcription = _transcription([" a", " e", " o"])
        corrected = apply_vocabulary_corrections(transcription, ["ANAC"])
        original = transcription["segments"][0]["words"]
        result = corrected["segments"][0]["words"]
        for o, r in zip(original, result):
            self.assertEqual(o["word"], r["word"])

    def test_unrelated_word_is_not_corrected(self):
        transcription = _transcription([" the", " weather", " today."])
        corrected = apply_vocabulary_corrections(transcription, ["ANAC", "VOEPASS"])
        for o, r in zip(transcription["segments"][0]["words"], corrected["segments"][0]["words"]):
            self.assertEqual(o["word"], r["word"])

    def test_empty_vocabulary_returns_same_object(self):
        transcription = _transcription([" ANAK"])
        self.assertIs(apply_vocabulary_corrections(transcription, []), transcription)

    def test_segment_text_is_rebuilt_after_correction(self):
        transcription = _transcription([" a", " ANAK,", " disse."])
        corrected = apply_vocabulary_corrections(transcription, ["ANAC"])
        self.assertEqual(corrected["segments"][0]["text"], " a ANAC, disse.")

    def test_does_not_mutate_input(self):
        transcription = _transcription([" ANAK"])
        apply_vocabulary_corrections(transcription, ["ANAC"])
        self.assertEqual(transcription["segments"][0]["words"][0]["word"], " ANAK")


class TestApplyWordOverrides(unittest.TestCase):

    def test_overrides_specific_word_by_index(self):
        transcription = _transcription([" a", " ANAK,", " disse."])
        corrected = apply_word_overrides(transcription, {1: "ANAC"})
        self.assertEqual(corrected["segments"][0]["words"][1]["word"], " ANAC,")

    def test_string_keys_are_accepted(self):
        transcription = _transcription([" a", " ANAK,", " disse."])
        corrected = apply_word_overrides(transcription, {"1": "ANAC"})
        self.assertEqual(corrected["segments"][0]["words"][1]["word"], " ANAC,")

    def test_index_spans_multiple_segments(self):
        transcription = {
            "segments": [
                {"id": 0, "start": 0.0, "end": 1.0, "text": " Hello",
                 "words": [{"word": " Hello", "start": 0.0, "end": 1.0, "probability": 0.9}]},
                {"id": 1, "start": 1.0, "end": 2.0, "text": " ANAK",
                 "words": [{"word": " ANAK", "start": 1.0, "end": 2.0, "probability": 0.9}]},
            ]
        }
        corrected = apply_word_overrides(transcription, {1: "ANAC"})
        self.assertEqual(corrected["segments"][1]["words"][0]["word"], " ANAC")
        self.assertEqual(corrected["segments"][0]["words"][0]["word"], " Hello")

    def test_no_overrides_returns_same_object(self):
        transcription = _transcription([" ANAK"])
        self.assertIs(apply_word_overrides(transcription, {}), transcription)

    def test_does_not_mutate_input(self):
        transcription = _transcription([" ANAK"])
        apply_word_overrides(transcription, {0: "ANAC"})
        self.assertEqual(transcription["segments"][0]["words"][0]["word"], " ANAK")


class TestApplyCorrectionsCombined(unittest.TestCase):

    def test_manual_override_wins_over_vocabulary_correction(self):
        transcription = _transcription([" ANAK"])
        # Vocabulary would correct to "ANAC", but the user manually decided "ANEEL" instead.
        corrected = apply_corrections(transcription, vocabulary=["ANAC"], word_overrides={0: "ANEEL"})
        self.assertEqual(corrected["segments"][0]["words"][0]["word"], " ANEEL")

    def test_vocabulary_applies_when_no_override_for_that_word(self):
        transcription = _transcription([" ANAK", " VOEPAZ"])
        corrected = apply_corrections(transcription, vocabulary=["ANAC", "VOEPASS"], word_overrides={1: "VoePass"})
        self.assertEqual(corrected["segments"][0]["words"][0]["word"], " ANAC")
        self.assertEqual(corrected["segments"][0]["words"][1]["word"], " VoePass")


if __name__ == "__main__":
    unittest.main()
