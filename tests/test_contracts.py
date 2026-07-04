import unittest
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.contracts.parser import RawNode, BaseParser, register_parser, get_parser
from src.contracts.llm import get_llm, OllamaLLM, OpenAICompatLLM
from src.contracts.embedder import get_embedder, OllamaEmbedder, OpenAICompatEmbedder
from src.parser.toc_extractor import build_tree_from_raw, TOCExtractor


class FakeParser(BaseParser):
    """Şirketin yazabileceği türden custom parser (motor kodu değişmeden takılır)."""
    name = "fake"

    def parse(self, file_path, progress_cb=None):
        return [
            RawNode("Yöntem", 1, "yöntem preamble"),
            RawNode("Veri Kaynakları", 2, "Iowa Mesonet arşivi"),
            RawNode("Sonuç", 1, "sonuç metni"),
        ]


class TestParserContract(unittest.TestCase):
    def test_registry_and_dynamic_import(self):
        register_parser("fake", FakeParser)
        self.assertIsInstance(get_parser("fake"), FakeParser)
        # "modul:Sinif" biçiminde harici sınıf import (modül-yol kimliğinden bağımsız kontrol)
        p = get_parser("tests.test_contracts:FakeParser")
        self.assertEqual(type(p).__name__, "FakeParser")
        self.assertEqual(p.name, "fake")

    def test_content_parser_chain(self):
        register_parser("fake", FakeParser)
        chain = TOCExtractor._content_parser_chain("pymupdf")
        self.assertEqual([type(c).__name__ for c in chain], ["PyMuPDFParser"])
        chain = TOCExtractor._content_parser_chain("auto")
        self.assertEqual([type(c).__name__ for c in chain], ["DoclingParser", "PyMuPDFParser"])
        chain = TOCExtractor._content_parser_chain("fake")   # özel parser + fail-safe
        self.assertEqual([type(c).__name__ for c in chain], ["FakeParser", "PyMuPDFParser"])

    def test_custom_parser_output_builds_tree(self):
        # Sahte parser'ın RawNode çıktısı motor tarafından uçtan uca ağaca dönüşür.
        raw = FakeParser().parse("x.pdf")
        tree = build_tree_from_raw(raw, max_depth=6)
        by_h = {n["heading"]: n for n in tree}
        self.assertEqual(by_h["Yöntem"]["is_leaf"], 0)                  # çocuğu var
        self.assertEqual(by_h["Veri Kaynakları"]["parent_id"], by_h["Yöntem"]["id"])
        self.assertEqual(by_h["Veri Kaynakları"]["path"], "Yöntem > Veri Kaynakları")
        self.assertEqual(by_h["Sonuç"]["is_leaf"], 1)
        self.assertIn("Iowa", by_h["Veri Kaynakları"]["content"])


class TestLLMEmbedderFactory(unittest.TestCase):
    def test_llm_factory_default_ollama(self):
        llm = get_llm("qwen3:8b")
        self.assertIsInstance(llm, OllamaLLM)
        self.assertEqual(llm.model, "qwen3:8b")

    def test_openai_compat_adapters_construct(self):
        # Ağ yok — yalnız yapılandırma doğrulaması
        o = OpenAICompatLLM(model="mistral", base_url="http://x/v1", api_key="k")
        self.assertTrue(o.base_url.endswith("/v1"))
        e = OpenAICompatEmbedder(model="bge", base_url="http://x/v1")
        self.assertEqual(e.model_key, "openai_compat:bge")

    def test_ollama_embedder_model_key_backward_compat(self):
        # Geriye uyum: ollama için salt model adı (eski koleksiyonlar düşmesin)
        self.assertEqual(OllamaEmbedder(model="bge-m3").model_key, "bge-m3")

    def test_nomic_prefix(self):
        self.assertEqual(OllamaEmbedder(model="nomic-embed-text").query_prefix, "search_query: ")
        self.assertEqual(OllamaEmbedder(model="bge-m3").query_prefix, "")


if __name__ == "__main__":
    unittest.main()
