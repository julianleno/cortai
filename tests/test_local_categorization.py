import unittest
from cortai.segmentation import categorize_local_text
from cortai import clip_selection


class TestLocalCategorization(unittest.TestCase):

    def test_categorize_question_explain(self):
        cat = categorize_local_text("Como fazer o melhor corte para o TikTok?")
        self.assertEqual(cat["category"], "question_explain")
        self.assertEqual(cat["label"], "Explicação & Dica")

    def test_categorize_viral_hook(self):
        cat = categorize_local_text("O maior segredo para ter visualizações incríveis no Reels!")
        self.assertEqual(cat["category"], "viral_hook")
        self.assertEqual(cat["label"], "Gancho de Impacto")

    def test_categorize_storytelling(self):
        cat = categorize_local_text("Um dia quando eu estava gravando um vídeo no estúdio...")
        self.assertEqual(cat["category"], "storytelling")
        self.assertEqual(cat["label"], "História & Narrativa")

    def test_categorize_data_stats(self):
        cat = categorize_local_text("Passo 1 de 3 dicas para aumentar o engajamento em 50%")
        self.assertEqual(cat["category"], "data_stats")
        self.assertEqual(cat["label"], "Dado & Ensinamento")

    def test_local_candidate_includes_category_metadata(self):
        class DummyCandidate:
            start = 0.0
            end = 5.0
            text = "Por que o algoritmo recomenda estes vídeos curtos?"

        suggestion = clip_selection.local_candidate_to_suggestion(DummyCandidate())
        self.assertIn("category", suggestion)
        self.assertEqual(suggestion["category"], "question_explain")
        self.assertIn("Explicação & Dica", suggestion["justificativa"])


if __name__ == "__main__":
    unittest.main()
