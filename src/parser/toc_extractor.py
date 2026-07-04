import os
import re
from typing import List, Dict, Any, Optional
import fitz  # PyMuPDF

from src import config
from src.contracts.parser import RawNode, BaseParser, register_parser, get_parser


class TOCExtractor:
    """
    PDF/Markdown belgelerinden hiyerarşik ağaç (TOC) çıkaran modül.

    Hiyerarşi kaynağı üç katmanlı önceliklendirme ile belirlenir (bkz. project_brain.md §6.2):
      1. Gömülü TOC / yer imi (`doc.get_toc()`) — en güvenilir, varsa öncelikli.
      2. Sezgisel: layout/font-boyutu analizi ile Markdown başlık çıkarımı.
    Çıkan ağaç `finalize_tree` ile sonlandırılır: derinlik sınırı (katlama) + yaprak işaretleme.
    """

    # ------------------------------------------------------------------ #
    #  Üst seviye giriş noktası
    # ------------------------------------------------------------------ #
    @staticmethod
    def extract_tree(pdf_path: str, progress_callback=None, max_depth: int = None,
                     parser: str = None) -> List[Dict[str, Any]]:
        """
        Bir PDF'ten sonlandırılmış hiyerarşik ağacı döndürür (katmanlı parser).

        Öncelik: (1) güvenilir gömülü TOC/yer imi (her parser için bedava), yoksa
        (2) içerik-tabanlı parser → `config.PARSER` ile seçilir:
          - "docling"/"auto": Docling (layout-model, alt-bölüm çözünürlüğü) kuruluysa onu,
            değilse PyMuPDF sezgisel yola düşer (fail-safe).
          - "pymupdf": doğrudan sezgisel (hızlı, bağımlılıksız).
        Tüm yollar `finalize_tree` ile sonlanır (veri modeli değişmez).
        """
        if max_depth is None:
            max_depth = config.MAX_TREE_DEPTH
        if parser is None:
            parser = config.PARSER
        if not os.path.exists(pdf_path):
            raise FileNotFoundError(f"PDF dosyası bulunamadı: {pdf_path}")

        # 1. Gömülü yer imi önceliği (parser-bağımsız, bedava)
        raw = BookmarkParser().parse(pdf_path)
        # 2. Yoksa içerik-tabanlı parser zinciri (auto: docling→pymupdf; sonu her zaman PyMuPDF)
        if not raw:
            for p in TOCExtractor._content_parser_chain(parser):
                raw = p.parse(pdf_path, progress_callback)
                if raw:
                    break

        # RawNode (IR) → motor: id/parent/path + finalize (veri modeli değişmez)
        return build_tree_from_raw(raw, max_depth)

    @staticmethod
    def _content_parser_chain(parser: str) -> List[BaseParser]:
        """
        config.PARSER → denenecek içerik parser'ları. Zincir her zaman PyMuPDF ile biter
        (fail-safe). Şirket parser'ı: kayıtlı ad ya da "paket.modul:Sinif".
        """
        pymupdf = PyMuPDFParser()
        if parser == "pymupdf":
            return [pymupdf]
        if parser in ("auto", "docling"):
            return [DoclingParser(), pymupdf]
        if parser == "chandra":
            return [pymupdf]  # Chandra adaptörü (Faz 4c) henüz yok
        try:
            return [get_parser(parser), pymupdf]  # özel/harici parser
        except Exception:
            return [pymupdf]

    @staticmethod
    def _try_docling(pdf_path: str) -> Optional[List[Dict[str, Any]]]:
        """
        Docling (IBM) ile ağaç çıkarımı — opsiyonel "iyi kat".

        Docling layout-model tabanlıdır; başlık hiyerarşisini font-heuristic'e bağlı kalmadan
        algılar (Word auto-numbering metinde olmasa bile alt-bölümleri ayrı düğüm yapar).
        Belgeyi Markdown'a çevirip (başlık seviyeleri korunur) mevcut `extract_toc_tree`'ye
        verir → tüm ağaç mantığı yeniden kullanılır.

        Docling kurulu değilse (`pip install docling`) veya hata verirse None döner →
        çağıran PyMuPDF sezgisel yola düşer (fail-safe; "16GB'da çalışır" vaadi korunur).

        Not (v1): Docling Markdown'ı `<!-- Page N -->` işareti taşımadığından sayfa atfı
        bölüm-seviyesine iner (start_page kesin sayfa vermez). Yapısal çözünürlük önceliklidir;
        sayfa-provenance ileride iterate_items ile iyileştirilebilir.
        """
        try:
            from docling.document_converter import DocumentConverter, PdfFormatOption
            from docling.datamodel.base_models import InputFormat
            from docling.datamodel.pipeline_options import PdfPipelineOptions
        except ImportError:
            return None

        import tempfile
        try:
            # Dijital PDF: OCR gereksiz (bellek/zaman ↓). Sayfa görüntülerini tutma.
            opts = PdfPipelineOptions()
            opts.do_ocr = False
            opts.generate_page_images = False
            converter = DocumentConverter(
                format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=opts)}
            )

            # SAYFA-PARÇALI dönüştürme: Docling büyük PDF'lerde belleği kümülatif tüketip
            # ~22. sayfada std::bad_alloc veriyor (16GB). PDF'i küçük parçalara bölüp her
            # parçayı ayrı dönüştürerek tepe belleği sınırlarız; markdown'ları birleştiririz.
            src = fitz.open(pdf_path)
            total = len(src)
            batch = max(1, config.DOCLING_BATCH_PAGES)
            md_parts = []
            for start in range(0, total, batch):
                end = min(start + batch, total)
                chunk = fitz.open()
                chunk.insert_pdf(src, from_page=start, to_page=end - 1)
                tmp = os.path.join(tempfile.gettempdir(), f"_aegis_docling_{start}.pdf")
                chunk.save(tmp)
                chunk.close()
                try:
                    res = converter.convert(tmp)
                    if getattr(res, "errors", None):
                        src.close()
                        return None  # parça kısmi başarısız → bütünlük bozulur, PyMuPDF'e düş
                    md_parts.append(TOCExtractor._docling_md_with_pages(res.document, page_offset=start))
                except Exception:
                    src.close()
                    return None
                finally:
                    try:
                        os.remove(tmp)
                    except OSError:
                        pass
            src.close()
            markdown = "\n\n".join(md_parts)
        except Exception:
            return None

        if not markdown or not markdown.strip():
            return None
        # Docling Markdown'ı başlıkları çoğu zaman tek seviyede verir; hiyerarşiyi başlık
        # metnindeki numaralandırmadan (1.1 → 2. seviye) yeniden kur.
        markdown = TOCExtractor._renumber_markdown_headings(markdown)
        nodes = TOCExtractor.extract_toc_tree(markdown)
        if len(nodes) <= 1:
            return None
        return nodes

    @staticmethod
    def _docling_md_with_pages(doc, page_offset: int = 0) -> str:
        """
        DoclingDocument → Markdown + `<!-- Page N -->` sayfa işaretleri.
        `export_to_markdown` sayfa bilgisini kaybeder; burada `iterate_items` ile her ögenin
        provenance sayfasını ekleriz (sayfa-düzeyli alıntı için). Başlık seviyesi sonra
        numaradan yeniden atanır. API farklı/başarısızsa export_to_markdown'a düşülür.
        `page_offset`: parçanın global başlangıç sayfası (0-tabanlı) — global sayfa = offset + page_no.
        """
        try:
            lines = []
            last_page = None
            for item, _lvl in doc.iterate_items():
                text = (getattr(item, "text", "") or "").strip()
                if not text:
                    continue
                prov = getattr(item, "prov", None)
                page = getattr(prov[0], "page_no", None) if prov else None
                if page is not None and page != last_page:
                    lines.append(f"\n<!-- Page {page_offset + page} -->\n")
                    last_page = page
                label = str(getattr(item, "label", "")).lower()
                if any(k in label for k in ("header", "title", "section")):
                    lines.append(f"\n## {text}\n")
                else:
                    lines.append(text)
            md = "\n".join(lines)
            if "##" in md:  # en az bir başlık yakalandıysa bu yolu kullan
                return md
        except Exception:
            pass
        return doc.export_to_markdown()

    @staticmethod
    def _renumber_markdown_headings(markdown: str) -> str:
        """
        Markdown başlık seviyelerini, başlık metnindeki bölüm numarasından yeniden atar:
        "1 GİRİŞ" → H1, "1.1 ..." → H2, "3.2.1 ..." → H3; numarasız başlık → H1.
        (Docling tüm başlıkları aynı seviyede verince nesting kaybolduğu için gerekli.)
        """
        out = []
        for line in markdown.split("\n"):
            m = re.match(r"^#{1,6}\s+(.*)$", line)
            if not m:
                out.append(line)
                continue
            text = m.group(1).strip()
            num = re.match(r"^(\d+(?:\.\d+)*)\b", text)
            level = (num.group(1).count(".") + 1) if num else 1
            out.append(f"{'#' * min(level, 5)} {text}")
        return "\n".join(out)


    @staticmethod
    def _bookmarks_usable(meaningful: List[List[Any]], page_count: int) -> bool:
        """
        Gömülü yer imleri güvenilir mi? Yer imleri ancak (a) yeterli sayıda ve (b) makul
        dağılımlıysa kullanılır. Slayt destelerinde olduğu gibi yer imleri seyrek/yanlış
        yerleşmişse (örn. bir bölüm belgenin >%25'ini kaplıyorsa), içerik dilimleri kayar
        → bu durumda daha ince ve hizalı yapı veren sezgisel yola düşülür.
        """
        if len(meaningful) < 3 or page_count <= 0:
            return False
        pages = sorted(e[2] for e in meaningful)
        bounds = pages + [page_count + 1]
        max_slice = max(bounds[i + 1] - bounds[i] for i in range(len(pages)))
        return (max_slice / page_count) <= 0.25

    # ------------------------------------------------------------------ #
    #  1. Yol: Gömülü yer imlerinden ağaç
    # ------------------------------------------------------------------ #
    @staticmethod
    def _build_from_bookmarks(doc, entries: List[List[Any]]) -> List[Dict[str, Any]]:
        """
        `doc.get_toc()` çıktısından (her giriş: [level, title, page]) ağaç kurar.
        Her düğümün içeriği, kendi başlangıç sayfasından bir sonraki girişin sayfasına
        kadar olan metindir (preamble dahil; çocuklar sonraki girişlerde gelir).
        """
        nodes: List[Dict[str, Any]] = []
        stack: Dict[int, int] = {}  # level -> node listesi indeksi
        page_count = len(doc)

        for i, (level, title, page) in enumerate(entries):
            title = title.strip()
            start_page = max(1, page)                       # 1-tabanlı
            next_page = entries[i + 1][2] if i + 1 < len(entries) else page_count + 1
            end_page = max(start_page, min(page_count, next_page - 1))

            # İçerik metni: [start_page-1 .. next_page-1) (0-tabanlı) sayfa aralığı.
            start_idx = start_page - 1
            end_idx = min(page_count, next_page - 1)
            content = "\n".join(
                doc[p].get_text() for p in range(start_idx, end_idx)
            ).strip() if end_idx > start_idx else ""

            parent_idx = None
            parent_path = ""
            for l in range(level - 1, 0, -1):
                if l in stack:
                    parent_idx = stack[l]
                    parent_path = nodes[parent_idx]["path"]
                    break

            full_path = f"{parent_path} > {title}" if parent_path else title
            node_idx = len(nodes)
            nodes.append({
                "id": node_idx + 1,
                "heading": title,
                "level": level,
                "parent_id": nodes[parent_idx]["id"] if parent_idx is not None else None,
                "path": full_path,
                "content": content,
                "start_page": start_page,
                "end_page": end_page,
                "start_line": None,
                "end_line": None,
            })
            stack[level] = node_idx
            for l in list(stack.keys()):
                if l > level:
                    del stack[l]

        return nodes

    # ------------------------------------------------------------------ #
    #  2. Yol: Sezgisel PDF -> Markdown
    # ------------------------------------------------------------------ #
    @staticmethod
    def is_rect_inside(rect_a, rect_b) -> bool:
        """A dikdörtgeninin B dikdörtgeninin içinde (küçük toleransla) olup olmadığını kontrol eder."""
        return (rect_a[0] >= rect_b[0] - 2 and
                rect_a[1] >= rect_b[1] - 2 and
                rect_a[2] <= rect_b[2] + 2 and
                rect_a[3] <= rect_b[3] + 2)

    @staticmethod
    def _heading_level(max_size: float, body_size: float, line_text: str, numbering) -> int:
        """Başlık seviyesini yazı boyutu farkından (veya numaralandırmadan) hesaplar."""
        if numbering:
            dots = line_text.split()[0].count(".")
            return min(5, max(1, dots + 1))
        diff = max_size - body_size
        if diff >= 6.0:
            return 1
        if diff >= 4.0:
            return 2
        if diff >= 2.0:
            return 3
        if diff >= 0.5:
            return 4
        return 5

    @staticmethod
    def convert_pdf_to_markdown(pdf_path: str, progress_callback=None) -> str:
        """
        PDF'i sayfa sayfa Markdown'a çevirir. Başlık tespiti **satır seviyesindedir**:
        bir satır ancak kısa + gövdeden büyük/kalın + cümle noktalamasıyla bitmiyorsa
        başlık sayılır. Bu, paragraf içi tek bir kalın kelimenin başlık sanılmasını
        (over-segmentation) engeller. Tablolar koordinatına göre araya yerleştirilir.
        Sayfa sınırları `<!-- Page N -->` yorumlarıyla işaretlenir.
        """
        if not os.path.exists(pdf_path):
            raise FileNotFoundError(f"PDF dosyası bulunamadı: {pdf_path}")

        doc = fitz.open(pdf_path)
        markdown_lines = []

        # 1. Aşama: gövde (body) yazı boyutunu belirle.
        # Kitaplarda ilk sayfalar telif/künye olduğundan, belgenin ortasından örnekleriz.
        font_sizes = []
        if len(doc) <= 5:
            sample_pages = list(range(len(doc)))
        else:
            start_page = max(1, len(doc) // 10)
            step = max(1, (len(doc) - start_page) // 10)
            sample_pages = [i for i in range(start_page, len(doc), step)][:10]

        for i in sample_pages:
            page_dict = doc[i].get_text("dict")
            for block in page_dict.get("blocks", []):
                for line in block.get("lines", []):
                    for span in line.get("spans", []):
                        font_sizes.append(round(span.get("size", 10), 1))

        if font_sizes:
            from collections import Counter
            body_font_size = Counter(font_sizes).most_common(1)[0][0]
        else:
            body_font_size = 10.0

        # 2. Aşama: sayfa sayfa dönüştürme
        for page_idx, page in enumerate(doc):
            if progress_callback:
                progress_callback(page_idx + 1, len(doc))
            markdown_lines.append(f"\n<!-- Page {page_idx + 1} -->\n")

            # A) Tabloları bul ve koordinatlarını sakla
            tables = page.find_tables()
            table_rects = []
            page_tables_markdown = {}
            for table in tables:
                table_rects.append(table.bbox)
                table_data = table.extract()
                if not table_data or len(table_data) < 1:
                    continue
                table_md = []
                col_count = len(table_data[0])
                for r_idx, row in enumerate(table_data):
                    clean_row = [str(c).replace("\n", " ").strip() if c is not None else "" for c in row]
                    table_md.append("| " + " | ".join(clean_row) + " |")
                    if r_idx == 0:
                        table_md.append("| " + " | ".join(["---"] * col_count) + " |")
                page_tables_markdown[table.bbox[1]] = "\n" + "\n".join(table_md) + "\n"

            # B) Metin bloklarını oku ve okuma sırasına göre sırala
            page_dict = page.get_text("dict")
            blocks = page_dict.get("blocks", [])
            blocks.sort(key=lambda b: (round(b["bbox"][1] / 10) * 10, b["bbox"][0]))

            # Sayfa-içi baskın (gövde) font boyutu. Slaytlarda fontlar globalde büyük
            # olduğundan mutlak eşik başlıkları kaçırır; sayfa-içi GÖRECELI oran daha
            # güvenilirdir (başlık, o slayttaki gövde metinden belirgin büyüktür).
            page_span_sizes = [
                round(s.get("size", 10.0), 1)
                for b in blocks if b.get("type") == 0
                for ln in b.get("lines", [])
                for s in ln.get("spans", []) if s.get("text", "").strip()
            ]
            if page_span_sizes:
                from collections import Counter as _Counter
                page_body_size = _Counter(page_span_sizes).most_common(1)[0][0]
            else:
                page_body_size = body_font_size

            rendered_table_coords = set()

            for block in blocks:
                if block.get("type") != 0:  # 0 = metin bloğu
                    continue
                block_bbox = block["bbox"]

                # Tablonun içindeki blokları atla (çift yazımı önle)
                if any(TOCExtractor.is_rect_inside(block_bbox, t) for t in table_rects):
                    continue

                # Bekleyen tabloları doğru y konumunda araya bas
                for t_y in list(page_tables_markdown.keys()):
                    if t_y < block_bbox[1] and t_y not in rendered_table_coords:
                        markdown_lines.append(page_tables_markdown[t_y])
                        rendered_table_coords.add(t_y)

                # Bloğu SATIR SATIR işle: başlıkları ayır, gövdeyi biriktir
                body_parts: List[str] = []
                for line in block.get("lines", []):
                    spans = [s for s in line.get("spans", []) if s.get("text", "").strip()]
                    if not spans:
                        continue
                    line_text = re.sub(r"\s+", " ", " ".join(s["text"].strip() for s in spans)).strip()
                    if not line_text:
                        continue

                    max_size = max(s.get("size", 10.0) for s in spans)
                    total_chars = sum(len(s["text"]) for s in spans) or 1
                    bold_chars = sum(len(s["text"]) for s in spans if (s.get("flags", 0) & 2))
                    bold_ratio = bold_chars / total_chars

                    # İÇİNDEKİLER (TOC) sayfasındaki noktalı-lider satırları başlık değil,
                    # gürültüdür (örn. "3.7 Kural Tabanlı Risk Motoru ........ 23"). Tamamen atla.
                    if re.search(r"\.{4,}\s*\d+\s*$", line_text):
                        continue

                    word_count = len(line_text.split())
                    ends_sentence = line_text[-1] in ".!?,:;"
                    # Güvenilir YAPISAL numaralandırma: çok-seviyeli alt-bölüm (3.7, 2.1.4) veya
                    # bölüm başlığı (1 GİRİŞ). Tek-seviyeli "1." LİSTE ögesidir, başlık DEĞİL
                    # (tezlerde amaç/bulgu listeleri böyle; numaralandırma tek başına başlık yapmaz).
                    # İlk bölüm 1-999 olmalı (ondalık değer değil): "3.7" evet, "0.993"/"2.024" hayır.
                    multi_num = re.match(r"^[1-9]\d{0,2}\.\d+(\.\d+){0,3}\.?\s+\S", line_text)
                    chapter_num = re.match(r"^\d+\s+[A-ZÇĞİÖŞÜ]", line_text)
                    structural_num = multi_num or chapter_num

                    is_short = word_count <= 18 and len(line_text) <= 140
                    big = max_size > body_font_size + 1.5                       # global (kitap)
                    bold_big = max_size > body_font_size + 0.5 and bold_ratio > 0.6
                    # Sayfa-içi göreceli: satır, o sayfanın gövde fontundan belirgin büyük mü?
                    size_ratio = max_size / page_body_size if page_body_size else 1.0
                    page_relative = size_ratio >= 1.18
                    is_heading = is_short and not ends_sentence and (big or bold_big or page_relative or bool(structural_num))

                    if is_heading:
                        if body_parts:
                            markdown_lines.append("\n" + " ".join(body_parts).strip() + "\n")
                            body_parts = []
                        level = TOCExtractor._heading_level(max_size, body_font_size, line_text, structural_num)
                        # Sadece sayfa-göreceli sinyalle yakalanan (slayt) başlıklarda mutlak
                        # fark küçük olabilir; seviyeyi orana göre de değerlendir, daha belirgini al.
                        if not structural_num:
                            if size_ratio >= 1.6:
                                level = min(level, 1)
                            elif size_ratio >= 1.4:
                                level = min(level, 2)
                            elif size_ratio >= 1.25:
                                level = min(level, 3)
                            elif page_relative:
                                level = min(level, 4)
                        markdown_lines.append(f"\n{'#' * level} {line_text}\n")
                    else:
                        body_parts.append(line_text)

                if body_parts:
                    markdown_lines.append("\n" + " ".join(body_parts).strip() + "\n")

            # Sayfa sonunda kalan tabloları ekle
            for t_y, t_md in page_tables_markdown.items():
                if t_y not in rendered_table_coords:
                    markdown_lines.append(t_md)
                    rendered_table_coords.add(t_y)

        doc.close()
        full_markdown = "\n".join(markdown_lines)
        full_markdown = re.sub(r"\n{3,}", "\n\n", full_markdown)
        return full_markdown.strip()

    @staticmethod
    def extract_toc_tree(markdown_text: str) -> List[Dict[str, Any]]:
        """
        Markdown'dan H1-H5 başlık hiyerarşisini stack tabanlı çıkarır.
        `<!-- Page N -->` işaretlerini izleyerek her düğüme başlangıç/bitiş sayfası atar.
        """
        lines = markdown_text.split("\n")
        nodes: List[Dict[str, Any]] = []
        stack: Dict[int, int] = {}
        current_node: Optional[Dict[str, Any]] = None
        current_page = 1

        for line_idx, line in enumerate(lines):
            line_num = line_idx + 1
            stripped = line.strip()

            # Sayfa işareti: içeriğe yazma, sadece sayfa sayacını güncelle
            page_match = re.match(r"<!--\s*Page\s+(\d+)\s*-->", stripped)
            if page_match:
                current_page = int(page_match.group(1))
                if current_node is not None:
                    current_node["end_page"] = current_page
                continue

            match = re.match(r"^(#{1,5})\s+(.+)$", stripped)
            if match:
                if current_node:
                    current_node["end_line"] = line_num - 1
                    current_node["end_page"] = current_page

                hashes, heading_text = match.groups()
                level = len(hashes)
                heading_text = heading_text.strip()

                parent_idx = None
                parent_path = ""
                for l in range(level - 1, 0, -1):
                    if l in stack:
                        parent_idx = stack[l]
                        parent_path = nodes[parent_idx]["path"]
                        break

                full_path = f"{parent_path} > {heading_text}" if parent_path else heading_text
                node_idx = len(nodes)
                new_node = {
                    "id": node_idx + 1,
                    "heading": heading_text,
                    "level": level,
                    "parent_id": nodes[parent_idx]["id"] if parent_idx is not None else None,
                    "path": full_path,
                    "content_lines": [],
                    "start_line": line_num,
                    "end_line": len(lines),
                    "start_page": current_page,
                    "end_page": current_page,
                }
                nodes.append(new_node)
                stack[level] = node_idx
                for l in list(stack.keys()):
                    if l > level:
                        del stack[l]
                current_node = new_node
            else:
                if current_node:
                    current_node["content_lines"].append(line)
                elif stripped:
                    current_node = {
                        "id": 1,
                        "heading": "Giriş",
                        "level": 1,
                        "parent_id": None,
                        "path": "Giriş",
                        "content_lines": [line],
                        "start_line": 1,
                        "end_line": len(lines),
                        "start_page": current_page,
                        "end_page": current_page,
                    }
                    nodes.append(current_node)
                    stack[1] = 0

        for node in nodes:
            node["content"] = "\n".join(node.pop("content_lines")).strip()

        if not nodes:
            nodes.append({
                "id": 1,
                "heading": "Belge İçeriği",
                "level": 1,
                "parent_id": None,
                "path": "Belge İçeriği",
                "content": markdown_text.strip(),
                "start_line": 1,
                "end_line": len(lines),
                "start_page": 1,
                "end_page": current_page,
            })

        return nodes

    # ------------------------------------------------------------------ #
    #  Ortak: ağaç sonlandırma (derinlik katlama + yaprak işaretleme)
    # ------------------------------------------------------------------ #
    @staticmethod
    def finalize_tree(nodes: List[Dict[str, Any]], max_depth: int) -> List[Dict[str, Any]]:
        """
        Ağacı sonlandırır:
          1. Derinlik katlama: `max_depth`'ten derin düğümler en yakın korunan ataya
             (markdown başlığıyla) gömülür — ağaç sığ ve gezinilebilir kalır.
          2. parent_id'leri korunan düğümlere yeniden bağlar.
          3. `is_leaf` işaretler (çocuğu olmayan düğüm = yaprak).
        Not: İçerik taşıyan her düğüm (iç düğüm preamble'ı dahil) vektörlenebilir;
        `is_leaf` yalnızca navigasyonun daha derine inebileceğini belirtir.
        """
        if not nodes:
            return nodes

        # 0. Gürültü filtresi: içindekiler/şekil-tablo listesi gibi salt-listeleme sayfaları
        # içerik düğümü değildir (özellikle Docling bunları ayrı düğüm yapıyor). Çıkar.
        # Türkçe İ→i̇ küçültme tuzağına düşmemek için ASCII-fold ile karşılaştır.
        # (Orphan kalan çocuklar aşağıdaki parent-remap ile köke bağlanır — güvenli.)
        import unicodedata

        def _fold(s):
            s = unicodedata.normalize("NFKD", s or "")
            return "".join(c for c in s if not unicodedata.combining(c)).lower().strip()

        noise = {"icindekiler", "sekiller listesi", "tablolar listesi",
                 "sekil listesi", "tablo listesi"}
        nodes = [n for n in nodes if _fold(n.get("heading")) not in noise]
        if not nodes:
            return nodes

        by_id = {n["id"]: n for n in nodes}

        # 1. Derinlik katlama
        kept = []
        for n in nodes:
            if n["level"] <= max_depth:
                kept.append(n)
            else:
                anc = n
                while anc is not None and anc["level"] > max_depth:
                    anc = by_id.get(anc["parent_id"])
                if anc is not None:
                    folded = f"\n\n{'#' * n['level']} {n['heading']}\n{n.get('content', '')}".rstrip()
                    anc["content"] = (anc.get("content", "") + folded).strip()
                    if n.get("end_page") is not None:
                        anc["end_page"] = n["end_page"]

        kept_ids = {n["id"] for n in kept}

        # 2. parent_id'leri korunan ataya yeniden bağla
        for n in kept:
            pid = n["parent_id"]
            while pid is not None and pid not in kept_ids:
                pid = by_id[pid]["parent_id"] if pid in by_id else None
            n["parent_id"] = pid

        # 3. is_leaf işaretle
        parents_with_children = {n["parent_id"] for n in kept if n["parent_id"] is not None}
        for n in kept:
            n["is_leaf"] = 0 if n["id"] in parents_with_children else 1
            n.setdefault("content", "")
            n.setdefault("start_page", None)
            n.setdefault("end_page", None)
            n.setdefault("start_line", None)
            n.setdefault("end_line", None)

        return kept


# ====================================================================== #
#  Motor: RawNode (IR) → ağaç  +  içerik parser adaptörleri (sözleşme)
# ====================================================================== #

def _rawnodes_from_full(full_nodes: List[Dict[str, Any]]) -> List[RawNode]:
    """Mevcut tam-düğüm çıktısını (id/parent/path'li) RawNode IR'ına indirger."""
    return [
        RawNode(heading=n["heading"], level=n["level"], content=n.get("content", ""),
                start_page=n.get("start_page"), end_page=n.get("end_page"))
        for n in full_nodes
    ]


def build_tree_from_raw(raw_nodes: List[RawNode], max_depth: int) -> List[Dict[str, Any]]:
    """
    RawNode listesinden hiyerarşik ağaç kurar: (heading, level) dizisinden stack ile
    id/parent/path üretir, sonra `finalize_tree` (depth fold + is_leaf + gürültü filtresi).
    Motorun tek yapısal giriş noktası — hangi parser üretmiş olursa olsun aynı işler.
    """
    nodes: List[Dict[str, Any]] = []
    stack: Dict[int, int] = {}
    for rn in raw_nodes:
        level = rn.level
        parent_idx, parent_path = None, ""
        for l in range(level - 1, 0, -1):
            if l in stack:
                parent_idx = stack[l]
                parent_path = nodes[parent_idx]["path"]
                break
        full_path = f"{parent_path} > {rn.heading}" if parent_path else rn.heading
        idx = len(nodes)
        nodes.append({
            "id": idx + 1,
            "heading": rn.heading,
            "level": level,
            "parent_id": nodes[parent_idx]["id"] if parent_idx is not None else None,
            "path": full_path,
            "content": rn.content or "",
            "start_page": rn.start_page,
            "end_page": rn.end_page,
            "start_line": None,
            "end_line": None,
        })
        stack[level] = idx
        for l in list(stack.keys()):
            if l > level:
                del stack[l]
    if not nodes:
        return []
    return TOCExtractor.finalize_tree(nodes, max_depth)


class BookmarkParser(BaseParser):
    """Gömülü TOC/yer imi (yeterince güvenilirse). Değilse boş liste → sonraki parser."""
    name = "bookmark"

    def parse(self, file_path, progress_cb=None) -> List[RawNode]:
        doc = fitz.open(file_path)
        try:
            page_count = len(doc)
            toc = doc.get_toc(simple=True)
            meaningful = [
                e for e in toc
                if e[1] and e[1].strip()
                and e[1].strip().lower() not in ("boş sayfa", "bos sayfa", "blank page")
            ]
            if not TOCExtractor._bookmarks_usable(meaningful, page_count):
                return []
            full = TOCExtractor._build_from_bookmarks(doc, meaningful)
        finally:
            doc.close()
        return _rawnodes_from_full(full)


class PyMuPDFParser(BaseParser):
    """Hızlı, bağımlılıksız sezgisel (font/koordinat + slayt-duyarlı). Varsayılan / fail-safe."""
    name = "pymupdf"

    def parse(self, file_path, progress_cb=None) -> List[RawNode]:
        markdown = TOCExtractor.convert_pdf_to_markdown(file_path, progress_callback=progress_cb)
        return _rawnodes_from_full(TOCExtractor.extract_toc_tree(markdown))


class DoclingParser(BaseParser):
    """Layout-model "iyi kat" (alt-bölüm çözünürlüğü). Kurulu değil/başarısızsa boş → PyMuPDF."""
    name = "docling"

    def parse(self, file_path, progress_cb=None) -> List[RawNode]:
        full = TOCExtractor._try_docling(file_path)
        return _rawnodes_from_full(full) if full else []


register_parser("bookmark", BookmarkParser)
register_parser("pymupdf", PyMuPDFParser)
register_parser("docling", DoclingParser)
