"""
Parser karşılaştırma harness'ı: aynı PDF'te PyMuPDF (sezgisel) vs Docling çıktısını
yan yana gösterir (düğüm sayısı, seviye dağılımı, ilk başlıklar). Faz 4 (Docling) kazancını
somut görmek için.

Kullanım (proje kökünden):
    pip install docling          # Docling'i denemek için (yoksa yalnız PyMuPDF görünür)
    python tools/compare_parsers.py "yol/belge.pdf"
"""
import os
import sys
from collections import Counter

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.parser.toc_extractor import TOCExtractor  # noqa: E402


def _stats(nodes):
    leaves = sum(1 for n in nodes if n.get("is_leaf"))
    levels = dict(sorted(Counter(n["level"] for n in nodes).items()))
    return f"{len(nodes)} düğüm (yaprak {leaves}, iç {len(nodes) - leaves}) · seviye {levels}"


def _dump(nodes, limit=30):
    for n in nodes[:limit]:
        kind = "Y" if n.get("is_leaf") else "İ"
        print(f"  L{n['level']} {kind} | {n['heading'][:60]}")
    if len(nodes) > limit:
        print(f"  ... (+{len(nodes) - limit} düğüm daha)")


def main():
    if len(sys.argv) < 2:
        print("Kullanım: python tools/compare_parsers.py <pdf_yolu>")
        return
    pdf = sys.argv[1]
    if not os.path.exists(pdf):
        print(f"Dosya yok: {pdf}")
        return

    print("=" * 60)
    print("PyMuPDF (sezgisel)")
    print("=" * 60)
    mu = TOCExtractor.finalize_tree(
        TOCExtractor.extract_toc_tree(TOCExtractor.convert_pdf_to_markdown(pdf)),
        config_max_depth(),
    )
    print(_stats(mu))
    _dump(mu)

    print("\n" + "=" * 60)
    print("Docling")
    print("=" * 60)
    dl = TOCExtractor._try_docling(pdf)
    if not dl:
        print("Docling kullanılamadı → `pip install docling` kurulu mu? (ya da dönüştürme başarısız)")
        print("Bu durumda sistem otomatik PyMuPDF'e düşer (fail-safe).")
        return
    dl = TOCExtractor.finalize_tree(dl, config_max_depth())
    print(_stats(dl))
    _dump(dl)

    print("\n" + "=" * 60)
    print(f"KARŞILAŞTIRMA: PyMuPDF {len(mu)} düğüm → Docling {len(dl)} düğüm")
    print("Docling daha fazla/derin düğüm veriyorsa alt-bölüm çözünürlüğü kazanılmıştır.")


def config_max_depth():
    from src import config
    return config.MAX_TREE_DEPTH


if __name__ == "__main__":
    main()
