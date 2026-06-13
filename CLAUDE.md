# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Proje Özeti

Aegis RAG; tamamen yerel (Ollama) çalışan, bulutsuz, gizlilik-öncelikli bir belge soru-cevap sistemidir. Klasik vektör-RAG'dan farkı, **çift hatlı (dual-pipeline)** mimaridir: bir **yapısal hat** (PDF'ten çıkarılan hiyerarşik başlık ağacı = PageIndex mantığı) ve bir **semantik hat** (ChromaDB vektör araması). LLM, akıl yürütmenin pahalı kısmını yapmak yerine, ağaç üzerinde *hangi düğümleri okuyacağına* yönlendirme (routing) yapar; ağır iş deterministik DB katmanına yıkılır.

İletişim dili Türkçe'dir (kod yorumları, prompt'lar, UI ve dokümanlar Türkçe).

## Çalıştırma Komutları

Tüm komutlar proje kök dizininden (`src/`'nin bulunduğu yer) çalıştırılır. Modüller `src.paket.modul` olarak import edildiğinden **çalışma dizini kök olmalıdır**.

```powershell
# Bağımlılıklar
pip install -r requirements.txt

# Ön koşul: Ollama çalışıyor olmalı + gerekli modeller çekilmiş olmalı
ollama pull qwen2.5:7b-instruct      # LLM (akıl yürütme/routing/NLI)
ollama pull nomic-embed-text         # embedding modeli (ChromaDB)

# Backend (port 8002 — README'deki 8000 GÜNCEL DEĞİL, kod 8002 kullanır)
python -m uvicorn src.backend.main:app --reload --port 8002

# UI (ayrı terminalde)
streamlit run src/ui/app.py          # http://localhost:8501

# Her şeyi tek tıkla (Windows): Ollama + backend + UI ayrı pencerelerde
run_aegis.bat
```

### Test ve Benchmark
```powershell
python -m unittest tests.test_parser            # tüm parser testleri
python -m unittest tests.test_parser.TestTOCExtractor.test_extract_toc_tree_count   # tek test
python tests/benchmark_suite.py                 # benchmark koşar, aegis_benchmark_report.md üretir
```
Not: `benchmark_suite.py`'deki testlerin çoğu mock/sabit veriyle çalışır (gerçek retrieval doğruluğunu ölçmez); rapor metni pazarlama dilindedir.

## Mimari (Büyük Resim)

İstek akışını anlamak için 4 dosyayı birlikte okumak gerekir: `toc_extractor.py` (parse) → `db_manager.py` (depolama) → `main.py` (sorgu orkestrasyonu) → `app.py` (UI).

### Veri katmanı — iki depo birlikte (`src/database/db_manager.py`)
Tek bir `DBManager` hem SQLite'ı hem ChromaDB'yi yönetir. **Her belge ikisine birden yazılır:**
- **SQLite** (`db/aegis_rag.db`): `documents`, `toc_nodes` (hiyerarşik ağaç + her düğüm için ilk 3 cümlelik `summary`), `toc_links` (regex ile çıkarılan DAG çapraz referansları). Yapısal hattın kaynağı.
- **ChromaDB** (`db/chroma_db/`): düğüm içerikleri `\n\n` ile paragraflara bölünüp `OllamaEmbeddingFunction` (nomic-embed-text) ile gömülür; metadata olarak `node_id`/`path` taşınır. Semantik hattın kaynağı. **İki hat `node_id` üzerinden kesişir** (heatmap bu eşlemeyle çizilir).
- `OllamaEmbeddingFunction` Ollama erişilemezse sessizce `DefaultEmbeddingFunction`'a düşer — bu farklı embedding uzayı/boyutu demektir (bilinen kırılganlık).

### Parse katmanı (`src/parser/toc_extractor.py`)
İki statik aşama: `convert_pdf_to_markdown` (PyMuPDF ile font boyutu/kalınlık + koordinat analizi → Markdown; tabloları koordinatına göre araya yerleştirir; gövde font boyutunu belgenin **ortasından** örnekleyerek bulur — kapak/künye sayfalarının sahte başlık üretmesini engellemek için) ve `extract_toc_tree` (Markdown `#` başlıklarından stack tabanlı hiyerarşi + `path` üretir). Başlık tespiti şu an **span seviyesindedir** (over-segmentation kaynağı).

### Sorgu orkestrasyonu — 5 katman (`src/backend/main.py`, `/api/query`)
Bu, sistemin kalbidir ve sırayla çalışır:
1. **Execution Policy** (deterministik, LLM'siz): sorgudaki anahtar kelimelerden mod seçer — `Multi-Subtree Synthesis` (kıyaslama kelimeleri), `Hybrid Reranked Fallback` (<3 kelime), yoksa `Hierarchical Single-Node`.
2. **Çift hat + heatmap**: ChromaDB top-10 → `lexical_semantic_rerank` (0.6·cosine + 0.4·kelime çakışması) → düğüm başına `hybrid_score` = heatmap.
3. **Routing (LLM)**: ısı haritalı tüm ağaç + soru LLM'e verilir, okunacak `node_id` listesi + `confidence` döner. `confidence == LOW` veya boş liste → **fallback** tetiklenir.
4. **Context Economy**: 4000 token katı bütçe. Seçilen düğümler (max 3) + DAG komşu **özetleri** eklenir; bütçe aşılırsa kırpılır. Fallback durumunda doğrudan reranked ChromaDB parçaları kullanılır.
5. **Sentez + Local NLI**: LLM cevabı üretir, sonra aynı model cevabı bağlama karşı `CONSISTENT`/`CONTRADICTION` diye denetler; çelişkide cevaba uyarı notu eklenir.

Yanıt; `answer` + `trace` (UI'da "Tree Trace" olarak gösterilen karar günlüğü) + `heatmap_scores` + `selected_node_ids` döner.

### UI (`src/ui/app.py`)
Tek dosyalık Streamlit. Sol sütun: ısı haritalı/seçili-düğüm vurgulu TOC ağacı. Sağ sütun: sohbet + dashboard rozetleri (policy/token/fallback) + Tree Trace expander. Yükleme, backend'in `BackgroundTasks` job'ını başlatıp `progress/{job_id}`'i 0.5sn aralıkla **bloklayarak poll eder** (timeout yok). Backend URL sabit: `http://127.0.0.1:8002`.

## Önemli Notlar / Tuzaklar

- **Port**: Kod her yerde **8002** kullanır (`app.py`, `run_aegis.bat`). README'deki 8000 eskidir. `127.0.0.1` bilinçli olarak `localhost` yerine sabitlenmiştir (IPv6 çakışması geçmişi — bkz. `project_brain.md`).
- **`project_brain.md`** projenin merkezî hafızasıdır: tasarım kararları, çözülmüş üretim hataları ve yol haritası burada tutulur. Mimari değişikliklerde güncel tutulmalıdır.
- **Git yok**: Proje versiyon kontrolünde değildir; bu yüzden geçmişte kök ve `aegis-rag/` altında **ayrışmış kopyalar** oluştu (temizlendi). Dosya kaybı riski yüksektir — `git init` önerilir.
- **Eşzamanlılık**: `DBManager` tek SQLite bağlantısını (`check_same_thread=False`) tüm thread'lerde paylaşır; eşzamanlı yazma/okumada kilit riski vardır.
- Sabitler dağınıktır (4000 token, 0.6/0.4 rerank ağırlıkları, model adları, port, top_k). Henüz merkezî bir config yoktur.
