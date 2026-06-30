import unittest
import os
import sys

# Proje kök dizinini Python yoluna ekleyelim
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.parser.toc_extractor import TOCExtractor

class TestTOCExtractor(unittest.TestCase):
    def setUp(self):
        self.sample_markdown = """# Bölüm 1: Giriş
Bu birinci bölümün ilk paragrafıdır.

## Alt Bölüm 1.1: Amaç
Bu alt bölümün amacı RAG sistemini doğrulamaktır.

## Alt Bölüm 1.2: Kapsam
Bu alt bölümün kapsamı yerel ağaç tabanlı aramadır.

### Detay 1.2.1: Algoritma
Burada hiyerarşik PageIndex algoritması detaylandırılmıştır.

# Bölüm 2: Metot ve Sonuçlar
İkinci ana bölümün içeriğidir.
"""

    def test_extract_toc_tree_count(self):
        nodes = TOCExtractor.extract_toc_tree(self.sample_markdown)
        # Toplam düğüm sayısı: H1(Giriş), H2(Amaç), H2(Kapsam), H3(Algoritma), H1(Metot) = 5
        self.assertEqual(len(nodes), 5)

    def test_extract_toc_tree_hierarchy(self):
        nodes = TOCExtractor.extract_toc_tree(self.sample_markdown)
        
        # Giriş başlığı seviye 1 olmalı
        self.assertEqual(nodes[0]["heading"], "Bölüm 1: Giriş")
        self.assertEqual(nodes[0]["level"], 1)
        self.assertIsNone(nodes[0]["parent_id"])
        
        # Amaç başlığı seviye 2 olmalı ve parent olarak Giriş'e bağlı olmalı
        self.assertEqual(nodes[1]["heading"], "Alt Bölüm 1.1: Amaç")
        self.assertEqual(nodes[1]["level"], 2)
        self.assertEqual(nodes[1]["parent_id"], nodes[0]["id"])
        self.assertEqual(nodes[1]["path"], "Bölüm 1: Giriş > Alt Bölüm 1.1: Amaç")
        
        # Detay 1.2.1 başlığı seviye 3 olmalı ve parent olarak Kapsam'a bağlı olmalı
        self.assertEqual(nodes[3]["heading"], "Detay 1.2.1: Algoritma")
        self.assertEqual(nodes[3]["level"], 3)
        self.assertEqual(nodes[3]["parent_id"], nodes[2]["id"])
        self.assertEqual(nodes[3]["path"], "Bölüm 1: Giriş > Alt Bölüm 1.2: Kapsam > Detay 1.2.1: Algoritma")

    def test_extract_toc_tree_content(self):
        nodes = TOCExtractor.extract_toc_tree(self.sample_markdown)

        # İçerik eşleşmelerini kontrol et
        self.assertIn("ilk paragrafıdır", nodes[0]["content"])
        self.assertIn("PageIndex algoritması", nodes[3]["content"])


class TestFinalizeTree(unittest.TestCase):
    def test_is_leaf_marking(self):
        nodes = TOCExtractor.extract_toc_tree(
            "# A\nicerik a\n## A1\nicerik a1\n# B\nicerik b\n"
        )
        final = TOCExtractor.finalize_tree(nodes, max_depth=6)
        by_h = {n["heading"]: n for n in final}
        # A'nın çocuğu (A1) var → iç düğüm; A1 ve B yaprak
        self.assertEqual(by_h["A"]["is_leaf"], 0)
        self.assertEqual(by_h["A1"]["is_leaf"], 1)
        self.assertEqual(by_h["B"]["is_leaf"], 1)

    def test_depth_folding(self):
        md = "# L1\nx\n## L2\ny\n### L3\nz\n#### L4\nderin icerik\n"
        nodes = TOCExtractor.extract_toc_tree(md)
        final = TOCExtractor.finalize_tree(nodes, max_depth=3)
        headings = [n["heading"] for n in final]
        # L4 katlanmalı (ayrı düğüm kalmamalı)
        self.assertNotIn("L4", headings)
        # L4'ün içeriği en yakın korunan ataya (L3) gömülmeli
        l3 = next(n for n in final if n["heading"] == "L3")
        self.assertIn("derin icerik", l3["content"])
        # Katlama sonrası L3 yaprak olmalı (artık çocuğu yok)
        self.assertEqual(l3["is_leaf"], 1)


class TestDoclingFailSafe(unittest.TestCase):
    def test_try_docling_returns_none_gracefully(self):
        # Docling kurulu değilse (ImportError) ya da dönüştürme hata verirse None dönmeli
        # (çağıran PyMuPDF'e düşer — fail-safe). Var olmayan dosyada da güvenli olmalı.
        self.assertIsNone(TOCExtractor._try_docling("___yok___.pdf"))


class TestBookmarksUsable(unittest.TestCase):
    def test_too_few_bookmarks(self):
        # 1 anlamlı yer imi -> kullanılamaz
        self.assertFalse(TOCExtractor._bookmarks_usable([[1, "A", 1]], 100))

    def test_sparse_bookmarks_rejected(self):
        # Sunum benzeri: bir bölüm belgenin >%25'ini kaplıyor -> sezgisele düş
        entries = [[1, "Gündem", 2], [1, "Bölüm", 23], [1, "Son", 55]]
        self.assertFalse(TOCExtractor._bookmarks_usable(entries, 57))

    def test_dense_bookmarks_accepted(self):
        # Kitap benzeri: dengeli dağılmış yer imleri -> kullan
        entries = [[1, f"Bölüm {i}", i * 8] for i in range(1, 13)]  # 12 giriş, ~8 sayfa arayla
        self.assertTrue(TOCExtractor._bookmarks_usable(entries, 100))


if __name__ == "__main__":
    unittest.main()
