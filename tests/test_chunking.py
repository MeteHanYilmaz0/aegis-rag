import unittest
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.database.db_manager import DBManager
from src import config


class TestChunkText(unittest.TestCase):
    def setUp(self):
        # __init__'i (SQLite/Chroma) çağırmadan yalnız _chunk_text'i test et
        self.db = DBManager.__new__(DBManager)

    def test_dehyphenation(self):
        # Satır-sonu tirelemesi birleşmeli: "Tür-\nkiye" -> "Türkiye"
        text = "Geçmiş Tür-\nkiye METAR verileri Iowa arşivinden indirildi."
        chunks = DBManager._chunk_text(self.db, text)
        joined = " ".join(chunks)
        self.assertIn("Türkiye", joined)
        self.assertNotIn("Tür- kiye", joined)
        self.assertNotIn("Tür-kiye", joined)

    def test_real_hyphen_preserved(self):
        # Gerçek tire (satır sonu değil) korunmalı
        chunks = DBManager._chunk_text(self.db, "Model operasyonel proxy-risk skoru üretir.")
        self.assertIn("proxy-risk", " ".join(chunks))

    def test_sentence_stays_whole_and_overlap(self):
        # Uzun metin birden çok chunk'a bölünür; cümleler bölünmez, örtüşme olur
        sentences = [f"Cümle numarası {i} burada yer alir." for i in range(60)]
        text = " ".join(sentences)
        chunks = DBManager._chunk_text(self.db, text)
        self.assertGreater(len(chunks), 1)
        for ch in chunks:
            self.assertLessEqual(len(ch), config.CHUNK_SIZE_CHARS + 200)  # makul üst sınır
        # ardışık chunk'larda örtüşme (ortak metin) beklenir
        self.assertTrue(any(
            chunks[i][-30:] and chunks[i][-30:] in chunks[i + 1]
            for i in range(len(chunks) - 1)
        ))

    def test_empty(self):
        self.assertEqual(DBManager._chunk_text(self.db, ""), [])
        self.assertEqual(DBManager._chunk_text(self.db, "   "), [])


if __name__ == "__main__":
    unittest.main()
