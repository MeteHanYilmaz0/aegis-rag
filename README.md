# Aegis RAG — Yerel Çift Hatlı Belge Zekâsı

Aegis RAG; tamamen **yerel donanımda** (Ollama) çalışan, bulutsuz, gizlilik-öncelikli bir belge soru-cevap sistemidir. Veriler bilgisayardan çıkmaz.

Klasik "her şeyi vektöre göm, top-k getir" RAG'ından farkı **çift hatlı (dual-pipeline)** mimarisidir:

- **Yapısal hat** — PDF'ten çıkarılan **hiyerarşik başlık ağacı** (PageIndex mantığı). LLM, ağaçta *hangi bölümü okuyacağına* yönlendirme (recursive descent) yapar.
- **Semantik hat** — ChromaDB vektör araması; seçilen bölümün **içinde** ince pasaj bulur.

İki hat **kesişir**: ağaç "NEREDE"yi (kaba navigasyon), vektör "TAM CÜMLE"yi (yaprak-içi arama) çözer. Böylece sistem, ağır akıl yürütmeyi pahalı LLM yerine deterministik DB/yapı katmanına yıkar — bu sayede **mütevazı yerel modellerle (8B) bile** iyi sonuç verir.

## Özellikler
- **%100 yerel ve gizli** — hiçbir veri dışarı çıkmaz.
- **Recursive descent navigasyon** — büyük belgelerde LLM'i yormadan doğru bölüme iniş.
- **Çift-hat kesişimi** — yapısal yönlendirme + ısı-haritası vektör sinyali birlikte (biri yanılırsa diğeri telafi eder).
- **Alıntılı cevaplar** — her yanıt bölüm yolu/sayfa atfı verir; bağlamda yoksa "bilgi yok" der (uydurmaz).
- **Şeffaflık** — "Tree Trace" ile descent kararları + ısı haritası UI'da görünür.
- **Streaming yanıt** — cevap token-token akar.

## Mimari (özet)
```
İNDEKSLEME:  Belge → Parser (hiyerarşik ağaç + LLM özet) → cümle-duyarlı chunk
             → SQLite (ağaç) + ChromaDB (bge-m3, cosine, yalnız içerik düğümleri)
SORGU:       Soru → ısı haritası → recursive descent → çift-hat kapsam
             → kapsam-içi pasaj araması → alıntılı sentez (streaming)
```
Ayrıntılı tasarım ve kararlar: [`project_brain.md`](project_brain.md) §6 ve [`CLAUDE.md`](CLAUDE.md).

## Kurulum ve Çalıştırma

### 1. Bağımlılıklar
```bash
pip install -r requirements.txt
```

### 2. Ollama + modeller
[Ollama](https://ollama.com)'yı kurup gerekli modelleri çekin:
```bash
ollama pull qwen3:8b        # LLM (navigasyon + sentez)
ollama pull bge-m3          # embedding (çok dilli/Türkçe)
```

### 3. Başlatma (port 8002)
```bash
# Backend
python -m uvicorn src.backend.main:app --reload --port 8002
# Arayüz (ayrı terminal)
streamlit run src/ui/app.py        # http://localhost:8501
```
Windows'ta tek tıkla: **`run_aegis.bat`** (Ollama + backend + UI).

## Test ve Değerlendirme
```bash
python -m unittest discover tests          # birim testler (parser/chunking/descent)
python tests/benchmark_suite.py            # golden-set değerlendirme (canlı backend gerekir)
```

## Yapılandırma
Tüm sabitler [`src/config.py`](src/config.py)'de toplanmıştır (model adları, token bütçesi, chunk boyutu, top_k, timeout'lar). `AEGIS_*` ortam değişkenleriyle ezilebilir; mimari model-agnostiktir.

## Yol Haritası
- **Faz 4:** Katmanlı parser (Docling) ile alt-bölüm çözünürlüğü; Chandra opsiyonel "en iyi kat".
