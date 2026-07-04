import unittest
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.engine.rerank import lexical_scores, rerank, char_ngrams


class TestLexicalRerank(unittest.TestCase):
    def test_idf_rare_term_wins(self):
        # Nadir/ayırt edici kelime (guardrail) yaygın kelimeden (risk) daha ağır olmalı.
        docs = ["risk risk risk risk risk", "guardrail kritik eşik koruma"]
        sc = lexical_scores("guardrail", docs)
        self.assertGreater(sc[1], sc[0])

    def test_morphology_via_char_ngram(self):
        # "arşiv" sorgusu, kelime tokeni eşleşmese de "arşivinden" içeren belgeyi bulmalı.
        docs = [
            "Iowa Mesonet arşivinden indirilen kayıtlarla oluşturuldu",
            "modelin genel eğitim süreci ve sonuçları",
        ]
        sc = lexical_scores("hangi arşiv", docs)
        self.assertGreater(sc[0], sc[1])

    def test_char_ngrams_basic(self):
        g = char_ngrams("abcd", n=3)
        self.assertEqual(g, {"abc", "bcd"})

    def test_rerank_sorts_and_scores(self):
        cands = [
            {"content": "alakasız metin", "similarity": 0.30},
            {"content": "guardrail kritik eşik", "similarity": 0.80},
        ]
        out = rerank("guardrail", cands, top_k=2)
        self.assertEqual(out[0]["content"], "guardrail kritik eşik")
        self.assertIn("hybrid_score", out[0])

    def test_empty(self):
        self.assertEqual(rerank("q", [], 3), [])
        self.assertEqual(lexical_scores("q", []), [])


if __name__ == "__main__":
    unittest.main()
