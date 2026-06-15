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
ollama pull qwen3:8b                  # LLM (navigasyon/routing/sentez) — varsayılan
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
- **SQLite** (`db/aegis_rag.db`): `documents`, `toc_nodes` (hiyerarşik ağaç; her düğümde `summary`, `is_leaf`, `start_page`/`end_page`, `token_count`). Yapısal hattın kaynağı. Şema sürümü `SCHEMA_VERSION` (PRAGMA user_version); değişince tablolar düşürülür → yeniden indeksleme gerekir. WAL + `_write_lock` ile eşzamanlı yazma korunur. (`toc_links`/regex DAG **kaldırıldı**.)
- **ChromaDB** (`db/chroma_db/`): **içerik taşıyan** düğümlerin içerikleri `\n\n` ile paragraflara bölünüp gömülür; metadata `node_id`/`path`/`heading`/`start_page` taşır. Embedding'ler `OllamaEmbedder` ile elle hesaplanıp Chroma'ya verilir (koleksiyonun kendi embedding fonksiyonu yok). **İki hat `node_id` üzerinden kesişir** (heatmap bu eşlemeyle çizilir; scoped arama `node_id ∈ {...}` ile yaprağa daraltılır).
- `OllamaEmbedder` **fail-fast**'tir (Ollama yoksa hata fırlatır, sessiz fallback yok) ve nomic için `search_document:`/`search_query:` ön-eki uygular.

### Parse katmanı (`src/parser/toc_extractor.py`)
Üst giriş noktası `extract_tree(pdf_path)`: önce gömülü TOC/yer imi (`doc.get_toc()`, ≥3 anlamlı giriş varsa) ile ağaç kurar; yoksa sezgisel yola düşer (`convert_pdf_to_markdown` → `extract_toc_tree`). `convert_pdf_to_markdown` font/koordinat analizi yapar; gövde font boyutunu belgenin **ortasından** örnekler. Başlık tespiti **satır seviyesindedir** (kısa + gövdeden büyük/kalın + cümle noktalamasıyla bitmeyen satır) — inline kalın kelimenin başlık sanılması (over-segmentation) giderildi. Tüm yollar `finalize_tree` ile sonlanır: **derinlik katlama** (`MAX_TREE_DEPTH`'ten derin düğümler ataya gömülür) + **`is_leaf`** işaretleme. `.md/.txt` için `extract_toc_tree` + `finalize_tree` ayrı çağrılır.

### Sorgu orkestrasyonu — 5 katman (`src/backend/main.py`, `/api/query`)
Bu, sistemin kalbidir ve sırayla çalışır:
1. **Execution Policy** (deterministik, LLM'siz): sorgudaki anahtar kelimelerden mod seçer — `Multi-Subtree Synthesis` (kıyaslama kelimeleri), `Hybrid Reranked Fallback` (<3 kelime), yoksa `Hierarchical Single-Node`.
2. **Çift hat + heatmap**: ChromaDB top-10 → `lexical_semantic_rerank` (0.6·cosine + 0.4·kelime çakışması) → düğüm başına `hybrid_score` = heatmap.
3. **Routing (LLM)**: ısı haritalı tüm ağaç + soru LLM'e verilir, okunacak `node_id` listesi + `confidence` döner. `confidence == LOW` veya boş liste → **fallback** tetiklenir.
4. **Context Economy**: 4000 token katı bütçe. Seçilen düğümler (max 3) eklenir; bütçe aşılırsa kırpılır. Fallback durumunda doğrudan reranked ChromaDB parçaları kullanılır. (Not: Faz 3'te bu katman recursive descent + yaprak-içi scoped aramayla değişecek.)
5. **Sentez + Local NLI**: LLM cevabı üretir, sonra aynı model cevabı bağlama karşı `CONSISTENT`/`CONTRADICTION` diye denetler; çelişkide cevaba uyarı notu eklenir.

Yanıt; `answer` + `trace` (UI'da "Tree Trace" olarak gösterilen karar günlüğü) + `heatmap_scores` + `selected_node_ids` döner.

### UI (`src/ui/app.py`)
Tek dosyalık Streamlit. Sol sütun: ısı haritalı/seçili-düğüm vurgulu TOC ağacı. Sağ sütun: sohbet + dashboard rozetleri (policy/token/fallback) + Tree Trace expander. Yükleme, backend'in `BackgroundTasks` job'ını başlatıp `progress/{job_id}`'i 0.5sn aralıkla **bloklayarak poll eder** (timeout yok). Backend URL sabit: `http://127.0.0.1:8002`.

## Önemli Notlar / Tuzaklar

- **Port**: Kod her yerde **8002** kullanır (`app.py`, `run_aegis.bat`). README'deki 8000 eskidir. `127.0.0.1` bilinçli olarak `localhost` yerine sabitlenmiştir (IPv6 çakışması geçmişi — bkz. `project_brain.md`).
- **`project_brain.md`** projenin merkezî hafızasıdır: tasarım kararları, çözülmüş üretim hataları ve yol haritası burada tutulur. Mimari değişikliklerde güncel tutulmalıdır.
- **Git yok**: Proje versiyon kontrolünde değildir; bu yüzden geçmişte kök ve `aegis-rag/` altında **ayrışmış kopyalar** oluştu (temizlendi). Dosya kaybı riski yüksektir — `git init` önerilir.
- **Eşzamanlılık**: `DBManager` tek SQLite bağlantısını (`check_same_thread=False`) tüm thread'lerde paylaşır; eşzamanlı yazma/okumada kilit riski vardır.
- **Sabitler `src/config.py`'de toplanmıştır** (model adları, token bütçesi, rerank ağırlıkları, top_k, timeout'lar, ağaç parametreleri). `AEGIS_*` ortam değişkenleriyle ezilebilir. Yeni sabit eklerken buraya koy. Varsayılan LLM `qwen3:8b` (thinking kapalı — `config.LLM_THINKING`).
- **Embedding:** `db_manager.OllamaEmbedder` fail-fast (Ollama yoksa sessizce yedeklenmez, hata fırlatır) ve nomic için `search_query:`/`search_document:` ön-eki uygular. ChromaDB koleksiyonu **cosine** uzaylıdır; eski L2 koleksiyon bulunursa otomatik yeniden oluşturulur (belgeleri yeniden indekslemek gerekir).
