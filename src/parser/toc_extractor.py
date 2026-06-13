import os
import re
from typing import List, Dict, Any, Optional
import fitz  # PyMuPDF

class TOCExtractor:
    """
    Markdown ve PDF dokümanlarından Hiyerarşik Ağaç (TOC) yapısı çıkaran gelişmiş modül.
    Görsel düzen algılama (layout-aware), yazı boyutu/kalınlığı analizi ve 
    gelişmiş tablo çıkarma yetenekleri sayesinde 1000+ sayfalık büyük ve karmaşık 
    PDF'leri yüksek doğrulukla çözümler.
    """

    @staticmethod
    def is_rect_inside(rect_a, rect_b) -> bool:
        """A dikdörtgeninin B dikdörtgeninin içinde veya çok yakınında olup olmadığını kontrol eder."""
        # rect = (x0, y0, x1, y1)
        return (rect_a[0] >= rect_b[0] - 2 and 
                rect_a[1] >= rect_b[1] - 2 and 
                rect_a[2] <= rect_b[2] + 2 and 
                rect_a[3] <= rect_b[3] + 2)

    @staticmethod
    def convert_pdf_to_markdown(pdf_path: str, progress_callback=None) -> str:
        """
        PDF dosyasını sayfa sayfa akışkan (streaming) olarak işler.
        Yazı boyutu (font size), yazı tipi kalınlığı (bold/flags) ve sayfa koordinatlarını analiz ederek
        okuma sırasına göre başlıkları ve gelişmiş tabloları Markdown formatına dönüştürür.
        1000+ sayfalık dokümanlarda bellek dostu çalışır.
        """
        if not os.path.exists(pdf_path):
            raise FileNotFoundError(f"PDF dosyası bulunamadı: {pdf_path}")

        doc = fitz.open(pdf_path)
        markdown_lines = []
        
        # 1. Aşama: Yazı boyutlarını analiz ederek varsayılan gövde metni (body text) boyutunu bulalım.
        # Belgenin ortasından ve çeşitli sayfalarından örnekler alarak en doğru gövde font boyutunu buluruz.
        font_sizes = []
        if len(doc) <= 5:
            sample_pages = list(range(len(doc)))
        else:
            # Kitaplarda ilk sayfalar telif/giriş olduğundan küçük fontludur.
            # Sayfa sayısının %10'undan başlayarak eşit aralıklarla 10 sayfa örnekleyelim.
            start_page = max(1, len(doc) // 10)
            step = max(1, (len(doc) - start_page) // 10)
            sample_pages = [i for i in range(start_page, len(doc), step)][:10]
            
        for i in sample_pages:
            page_dict = doc[i].get_text("dict")
            for block in page_dict.get("blocks", []):
                for line in block.get("lines", []):
                    for span in line.get("spans", []):
                        font_sizes.append(round(span.get("size", 10), 1))
        
        # En sık tekrarlanan yazı boyutunu (mode) gövde boyutu olarak belirle
        if font_sizes:
            from collections import Counter
            body_font_size = Counter(font_sizes).most_common(1)[0][0]
        else:
            body_font_size = 10.0

        # 2. Aşama: Sayfa sayfa yüksek doğruluklu dönüştürme işlemi
        for page_idx, page in enumerate(doc):
            if progress_callback:
                progress_callback(page_idx + 1, len(doc))
            markdown_lines.append(f"\n<!-- Page {page_idx + 1} -->\n")
            
            # A) Sayfadaki tabloları gelişmiş motor ile bul ve koordinatlarını kaydet
            tables = page.find_tables()
            table_rects = []
            page_tables_markdown = {}
            
            for t_idx, table in enumerate(tables):
                table_rects.append(table.bbox) # (x0, y0, x1, y1)
                
                # Tablo verisini Markdown tablosuna çevir
                table_data = table.extract()
                if not table_data or len(table_data) < 1:
                    continue
                    
                table_md = []
                col_count = len(table_data[0])
                
                for r_idx, row in enumerate(table_data):
                    # None değerleri boş stringe çevir
                    clean_row = [str(cell).replace("\n", " ").strip() if cell is not None else "" for cell in row]
                    table_md.append("| " + " | ".join(clean_row) + " |")
                    if r_idx == 0:
                        # Seperatör satırı
                        table_md.append("| " + " | ".join(["---"] * col_count) + " |")
                
                # Bu tablonun y koordinatına göre sayfaya yerleştirilmek üzere kaydet
                page_tables_markdown[table.bbox[1]] = "\n" + "\n".join(table_md) + "\n"

            # B) Sayfadaki tüm yazıları zengin sözlük (dict) formatında çek
            page_dict = page.get_text("dict")
            blocks = page_dict.get("blocks", [])
            
            # Blokları okuma sırasına göre sırala (Yukarıdan aşağıya, soldan sağa)
            # 2 sütunlu düzenleri de desteklemek için y koordinatı toleranslı sıralama yaparız
            blocks.sort(key=lambda b: (round(b["bbox"][1] / 10) * 10, b["bbox"][0]))
            
            rendered_table_coords = set()
            
            for block in blocks:
                # Eğer blok bir görsel veya metin içermiyorsa atla
                if block.get("type") != 0: # 0 = Metin bloğu
                    continue
                
                block_bbox = block["bbox"]
                
                # C) Eğer bu blok herhangi bir tablonun İÇİNDEYSE, çift yazdırmamak için atla
                is_inside_table = False
                for t_rect in table_rects:
                    if TOCExtractor.is_rect_inside(block_bbox, t_rect):
                        is_inside_table = True
                        break
                if is_inside_table:
                    continue
                
                # Blok içindeki satırları birleştirerek paragrafı/başlığı oluştur
                block_text_runs = []
                
                for line in block.get("lines", []):
                    line_spans = line.get("spans", [])
                    for span in line_spans:
                        text = span.get("text", "").strip()
                        if not text:
                            continue
                            
                        size = span.get("size", 10.0)
                        flags = span.get("flags", 0)
                        is_bold = bool(flags & 2) # fitz bold bayrağı
                        
                        # D) Gelişmiş Başlık Tespiti (Font Boyutu, Kalınlık ve Desen Analizi)
                        # Eğer yazı boyutu gövde metninden büyükse ve kalınsa başlık olma ihtimali yüksektir
                        is_likely_heading = (size > body_font_size + 1.5) or (size > body_font_size + 0.5 and is_bold)
                        
                        # Sayısal/Hiyerarşik başlık deseni kontrolü (Örn: 1. Giriş veya 2.1.2 Metot)
                        numbering_pattern = re.match(r'^\d+(\.\d+){0,4}\.?\s+[A-ZÇĞİÖŞÜa-zçğıöşü]', text)
                        
                        if is_likely_heading or numbering_pattern:
                            # Seviye hesaplama (Yazı boyutuna göre kademelendir)
                            diff = size - body_font_size
                            if diff >= 6.0:
                                level = 1
                            elif diff >= 4.0:
                                level = 2
                            elif diff >= 2.0:
                                level = 3
                            elif diff >= 0.5:
                                level = 4
                            else:
                                level = 5
                                
                            # Eğer numaralandırma deseni varsa, seviyeyi oradan da teyit et
                            if numbering_pattern:
                                dots_count = text.split()[0].count('.')
                                # Nokta sayısına göre seviyeyi ayarla
                                level = min(5, max(1, dots_count + 1))
                            
                            hashes = "#" * level
                            block_text_runs.append(f"\n{hashes} {text}\n")
                        else:
                            # Normal metin
                            # Kalın kelimeleri markdown kalın yapalım
                            if is_bold and len(text) < 30:
                                block_text_runs.append(f" **{text}** ")
                            else:
                                block_text_runs.append(text)
                
                block_content = " ".join(block_text_runs).strip()
                # Çoklu boşlukları temizle ve düzelt
                block_content = re.sub(r'\s+', ' ', block_content)
                block_content = block_content.replace(" \n ", "\n").replace("\n ", "\n").replace(" \n", "\n")
                
                # Tabloları araya doğru y koordinatında yerleştirme mantığı
                # Eğer bloğumuzun y koordinatı bir tablonun y koordinatını geçtiyse, o tabloyu araya bas
                for t_y in list(page_tables_markdown.keys()):
                    if t_y < block_bbox[1] and t_y not in rendered_table_coords:
                        markdown_lines.append(page_tables_markdown[t_y])
                        rendered_table_coords.add(t_y)
                
                if block_content:
                    markdown_lines.append(f"\n{block_content}\n")
            
            # Sayfa bittiğinde henüz yazdırılmamış tablolar varsa sayfaya ekle
            for t_y, t_md in page_tables_markdown.items():
                if t_y not in rendered_table_coords:
                    markdown_lines.append(t_md)
                    rendered_table_coords.add(t_y)

        # Tüm satırları birleştir ve gereksiz boşlukları optimize et
        full_markdown = "\n".join(markdown_lines)
        full_markdown = re.sub(r'\n{3,}', '\n\n', full_markdown)
        return full_markdown.strip()

    @staticmethod
    def extract_toc_tree(markdown_text: str) -> List[Dict[str, Any]]:
        """
        Markdown metninden kural tabanlı olarak H1-H5 başlık hiyerarşisini çıkarır.
        Düzleştirilmiş ağaç (Flat-Tree Selection) için her düğümün üst düğümünü,
        hiyerarşik yolunu (path), satır aralığını ve içeriğini belirler.
        """
        lines = markdown_text.split('\n')
        nodes: List[Dict[str, Any]] = []
        
        # Hiyerarşiyi takip etmek için bir yığın (stack) tutuyoruz.
        # stack[level] = node_index
        stack: Dict[int, int] = {}
        
        current_node: Optional[Dict[str, Any]] = None
        
        for line_idx, line in enumerate(lines):
            line_num = line_idx + 1
            # Başlık satırını kontrol et (örn: # Başlık, ## Başlık)
            match = re.match(r'^(#{1,5})\s+(.+)$', line.strip())
            
            if match:
                # Önceki aktif düğümün bitiş satırını güncelle
                if current_node:
                    current_node['end_line'] = line_num - 1

                hashes, heading_text = match.groups()
                level = len(hashes)
                heading_text = heading_text.strip()
                
                # Parent tespiti: Kendinden küçük seviyedeki en son aktif başlığı bul
                parent_idx = None
                parent_path = ""
                for l in range(level - 1, 0, -1):
                    if l in stack:
                        parent_idx = stack[l]
                        parent_path = nodes[parent_idx]['path']
                        break
                
                # Tam hiyerarşik yol (path) hesapla
                full_path = f"{parent_path} > {heading_text}" if parent_path else heading_text
                
                node_idx = len(nodes)
                new_node = {
                    "id": node_idx + 1,
                    "heading": heading_text,
                    "level": level,
                    "parent_id": nodes[parent_idx]['id'] if parent_idx is not None else None,
                    "path": full_path,
                    "content_lines": [],
                    "start_line": line_num,
                    "end_line": len(lines)  # Şimdilik son satıra kadar varsayalım
                }
                
                nodes.append(new_node)
                stack[level] = node_idx
                
                # Kendinden daha derin tüm seviyeleri yığından temizle
                for l in list(stack.keys()):
                    if l > level:
                        del stack[l]
                        
                current_node = new_node
            else:
                # Başlık olmayan satırları aktif düğüme ekle
                if current_node:
                    current_node['content_lines'].append(line)
                else:
                    # Eğer henüz hiç başlık yoksa ve içerik varsa, sanal bir "Giriş" düğümü açalım
                    if line.strip():
                        current_node = {
                            "id": 1,
                            "heading": "Giriş",
                            "level": 1,
                            "parent_id": None,
                            "path": "Giriş",
                            "content_lines": [line],
                            "start_line": 1,
                            "end_line": len(lines)
                        }
                        nodes.append(current_node)
                        stack[1] = 0

        # Kalan içerik satırlarını birleştirip 'content' alanına yazalım ve son end_line'ları netleştirelim
        for node in nodes:
            node['content'] = "\n".join(node['content_lines']).strip()
            del node['content_lines']  # Bellek optimizasyonu

        # Eğer hiç düğüm çıkarılamadıysa boş dönmesin, tüm belgeyi tek düğüm yapalım
        if not nodes:
            nodes.append({
                "id": 1,
                "heading": "Belge İçeriği",
                "level": 1,
                "parent_id": None,
                "path": "Belge İçeriği",
                "content": markdown_text.strip(),
                "start_line": 1,
                "end_line": len(lines)
            })

        return nodes
