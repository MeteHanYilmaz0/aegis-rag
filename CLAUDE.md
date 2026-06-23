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
ollama pull bge-m3                    # embedding modeli (ChromaDB) — çok dilli/Türkçe, varsayılan

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
- `OllamaEmbedder` **fail-fast**'tir (Ollama yoksa hata fırlatır, sessiz fallback yok). Varsayılan embedding **`bge-m3`** (çok dilli/Türkçe, 1024d, ön-ek istemez); nomic seçilirse `search_document:`/`search_query:` ön-eki uygular. Koleksiyon metadata'sında `embed_model` tutulur; model değişince (boyut farkı) koleksiyon otomatik yeniden oluşturulur → yeniden indeksleme gerekir.

### Parse katmanı (`src/parser/toc_extractor.py`)
Üst giriş noktası `extract_tree(pdf_path)`: gömülü TOC/yer imini **yalnız güvenilirse** kullanır (`_bookmarks_usable`: ≥3 giriş VE en büyük bölüm belgenin ≤%25'i — slayt destelerindeki seyrek/yanlış-yerleşmiş yer imleri içerik kaymasına yol açtığından elenir), aksi halde sezgisel yola düşer (`convert_pdf_to_markdown` → `extract_toc_tree`). Başlık tespiti **satır seviyesinde** ve **slayt-duyarlıdır**: mutlak font eşiği (kitap) + **sayfa-içi göreceli oran** (`size/page_body ≥ 1.18`, slaytlarda gövde font globalde büyük olduğu için şart) + kalın + numaralandırma; kısa ve cümle noktalamasıyla bitmeyen satırlar. Tüm yollar `finalize_tree` ile sonlanır: **derinlik katlama** (`MAX_TREE_DEPTH`) + **`is_leaf`**. `.md/.txt` için `extract_toc_tree` + `finalize_tree` ayrı çağrılır.

### Sorgu orkestrasyonu — recursive descent (`src/backend/main.py`, `/api/query`, Faz 3)
Bu, sistemin kalbidir ve sırayla çalışır:
1. **Heatmap (global çift-hat)**: ChromaDB top-k → `lexical_semantic_rerank` (0.6·cosine + 0.4·kelime çakışması) → düğüm başına `hybrid_score`. UI ısı haritası + descent ipucu olarak kullanılır.
2. **Recursive descent** (`run_recursive_descent`): kök çocuklarından başlayıp seviye seviye iner. Her turda frontier düğümleri (başlık + **özet** + ısı) LLM'e sunulur; LLM JSON döndürür: `select` (doğrudan oku), `descend` (alt-başlığına in), `confidence`. İç düğüm `descend`, yaprak `select`/hedef olur. `MAX_DESCENT_CALLS` ve `_cap_frontier` (geniş seviyelerde ısıya göre budama) ile sınırlı. Hedef yoksa veya `confidence==LOW` → fallback.
3. **Context Economy**: `TOKEN_BUDGET` katı bütçe. (a) Hedef düğümlerin **özetleri** (listeleme/genel-bakış için), (b) **yaprak-içi scoped arama**: `query_chroma(node_ids=alt-ağaç)` ile seçilen kapsam İÇİNDE pasajlar. Fallback'te tüm belge üzerinde global reranked pasajlar.
4. **Sentez**: LLM yalnız bağlamla, **alıntılı** (bölüm yolu/sayfa) ve "yoksa belirt" kuralıyla cevap üretir. (Self-NLI **kaldırıldı** — testlerde güvenilmez çıktı; yerini grounding/alıntı aldı.)

Yanıt; `answer` + `trace` (UI'da "Tree Trace") + `heatmap_scores` + `selected_node_ids` (hedef düğümler) + `execution_policy` (`Recursive Descent`/`Semantik Fallback`) + `fallback_triggered` döner. Descent yardımcıları için testler: `tests/test_descent.py`.

### UI (`src/ui/app.py`)
Tek dosyalık Streamlit. Sol sütun: ısı haritalı/seçili-düğüm vurgulu TOC ağacı. Sağ sütun: sohbet + dashboard rozetleri (policy/token/fallback) + Tree Trace expander. Yükleme, backend'in `BackgroundTasks` job'ını başlatıp `progress/{job_id}`'i 0.5sn aralıkla **bloklayarak poll eder** (timeout yok). Backend URL sabit: `http://127.0.0.1:8002`.

## Önemli Notlar / Tuzaklar

- **Port**: Kod her yerde **8002** kullanır (`app.py`, `run_aegis.bat`). README'deki 8000 eskidir. `127.0.0.1` bilinçli olarak `localhost` yerine sabitlenmiştir (IPv6 çakışması geçmişi — bkz. `project_brain.md`).
- **`project_brain.md`** projenin merkezî hafızasıdır: tasarım kararları, çözülmüş üretim hataları ve yol haritası burada tutulur. Mimari değişikliklerde güncel tutulmalıdır.
- **Git yok**: Proje versiyon kontrolünde değildir; bu yüzden geçmişte kök ve `aegis-rag/` altında **ayrışmış kopyalar** oluştu (temizlendi). Dosya kaybı riski yüksektir — `git init` önerilir.
- **Eşzamanlılık**: `DBManager` tek SQLite bağlantısını (`check_same_thread=False`) tüm thread'lerde paylaşır; eşzamanlı yazma/okumada kilit riski vardır.
- **Sabitler `src/config.py`'de toplanmıştır** (model adları, token bütçesi, rerank ağırlıkları, top_k, timeout'lar, ağaç parametreleri). `AEGIS_*` ortam değişkenleriyle ezilebilir. Yeni sabit eklerken buraya koy. Varsayılan LLM `qwen3:8b` (thinking kapalı — `config.LLM_THINKING`).
- **Embedding:** `db_manager.OllamaEmbedder` fail-fast (Ollama yoksa sessizce yedeklenmez, hata fırlatır) ve nomic için `search_query:`/`search_document:` ön-eki uygular. ChromaDB koleksiyonu **cosine** uzaylıdır; eski L2 koleksiyon bulunursa otomatik yeniden oluşturulur (belgeleri yeniden indekslemek gerekir).
