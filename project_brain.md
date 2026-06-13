# 🛡️ Aegis RAG: Proje Beyni (Project Brain)

Bu belge, **Aegis RAG (Frontier Local Document Intelligence)** projesinin tüm tasarım kararlarını, mimari akışını, veritabanı yapısını, çözülen kritik üretim hatalarını ve yol haritasını bir arada tutan merkezi referans kılavuzudur.

---

## 🧭 1. Proje Kimliği ve Temel Vizyon

Aegis RAG; bulut bağımlılığı olmayan, tamamen yerel donanımda (CPU/GPU) çalışan, **PageIndex (Yapısal Ağaç)** orkestrasyonu ile **Semantik RAG** katmanlarını birleştiren çift hatlı bir yerel bilgi işletim sistemidir.

* **Donanım Hedefi:** 16GB RAM, yerel CPU veya düşük bütçeli tüketici GPU'su.
* **Hız Hedefi:** Veritabanı seviyesinde sorgu eşleştirme latansı < 1.0 ms.
* **Güvenlik Hedefi:** LLM halüsinasyonlarını engelleyen sıfır bağımlılıklı yerel doğrulama (Local NLI).
* **Modeller:**
  - Dil Modeli (LLM): `qwen2.5:7b-instruct` (Ollama üzerinden)
  - Vektör Modeli (Embedding): `nomic-embed-text:latest` (Ollama üzerinden)

---

## 🏗️ 2. Çalışma ve Mimari Akışı

Sistem, bir belgenin yüklenmesinden kullanıcının sorusunun yanıtlanmasına kadar **5 temel katmanda** çalışır:

```mermaid
graph TD
    A[Belge Girişi: PDF/MD] --> B[1. TOCExtractor: Sayfa Yapısı & Yazı Boyutu Analizi]
    B --> C[2. Çift Katmanlı Veritabanı Modeli]
    C -->|Hiyerarşik Ağaç & DAG Bağları| C1[SQLite DB: toc_nodes & toc_links]
    C -->|Semantik Parçalar & Vektörler| C2[ChromaDB Vector Store]
    
    D[Kullanıcı Sorusu] --> E[3. Explicit Execution Policy Router]
    E -->|Single-Node / Multi-Subtree| E1[SQLite Hiyerarşi Modu]
    E -->|Genel / Kısa Sorgu| E2[ChromaDB Semantik Mod]
    
    E1 --> F[4. Bağlam Ekonomisi & Hibrit Rerank]
    E2 --> F
    F -->|4000 Token Katı Bütçe & DAG Komşu Özetleri| G[5. Yerel LLM Sentez Modülü]
    G --> H[AI Aday Cevabı]
    H --> I{Local NLI Tutarlılık Süzgeci}
    I -->|Consistent| J[Doğrulanmış Cevap]
    I -->|Contradiction| K[Cevap + Hallucination Uyarı Notu]
```

---

## 🛠️ 3. Çözülen Kritik Üretim Hataları (Resolved Bugs)

Projenin geliştirilmesi sırasında yerel Windows ortamında karşılaşılan ve çözülen kritik sorunlar ve mühendislik yaklaşımları:

### 🔴 Hata 1: Soket Bağlantı Çakışması (WinError 10013 - CLOSE_WAIT)
* **Semptom:** FastAPI backend başlatılmaya çalışıldığında `Erişim izinlerince izin verilmeyen bir şekilde bir yuvaya erişilmeye çalışıldı` hatası alınıyor ve Streamlit arayüzünde "Backend Hata" gösteriliyordu.
* **Teşhis:** Windows üzerinde `8000` portunu tutan ancak yanıt vermeyen bir zombie Python süreci (PID `30500`) soketi `CLOSE_WAIT` durumunda kilitli bırakmıştı.
* **Çözüm:** Tüm sistem yapılandırmaları (`app.py`, `run_aegis.bat` ve başlatma parametreleri) çakışmasız çalışan **`8002`** portuna taşındı. Ayrıca local isteklerin DNS çözümlemesinde IPv6 çakışması yaşamaması için `localhost` yerine kararlı IPv4 adresi olan **`127.0.0.1`** adresi sabitlendi.

### 🔴 Hata 2: PDF Parser Başlık Aşırı-Bölümleme (Over-Segmentation) Hatası
* **Semptom:** 3.9 MB'lık bir roman yüklendiğinde veritabanında **3.325 adet düğüm** oluşuyor, neredeyse tüm düğümlerin içerik uzunluğu **0 byte** kalıyor ve LLM *"Mevlut hakkında belgede bilgi yoktur"* diyordu.
* **Teşhis:** `TOCExtractor` sınıfı, gövde yazı boyutunu belirlemek için sadece ilk 5 sayfayı örnekliyordu. Romanın ilk 5 sayfası telif/künye sayfaları olduğundan küçük fontluydu (`8.0` boyutunda). Romanın asıl hikaye sayfaları ise `10.5` boyutundaydı. Parser, `10.5 > 8.0 + 1.5` kuralından ötürü **romandaki tüm normal paragrafları başlık sanarak** `###` ile işaretledi. Kitap binlerce boş düğüme bölündü ve metin içeriği kayboldu.
* **Çözüm:** Yazı boyutu analiz algoritması güncellendi. İlk 5 sayfa yerine, belgenin ortasından (%10'luk kısımdan sonrası) **eşit aralıklarla dağıtılmış 10 sayfa** örneklenerek gerçek gövde yazı boyutu (`10.5`) hatasız tespit edildi. Paragrafların başlık sanılması engellendi, düğüm sayısı 3.325'ten anlamlı hiyerarşik başlıklara düştü ve içerikler başarıyla SQLite'a yazıldı.

### 🔴 Hata 3: Sağlık Kontrolü Zaman Aşımı (Read Timeout) Hatası
* **Semptom:** Arayüz düzgün çalışırken bir süre sonra veya ilk açılışta `Read timed out. (read timeout=3)` hatası ile sistem durumu kırmızıya ("Backend Hata") dönüyordu.
* **Teşhis:** Arayüzün sağlık kontrolü zaman aşımı süresi `3 saniye` idi. Backend `/api/health` fonksiyonu ise Ollama'ya mükerrer (iki kez) istek gönderiyordu. Yerel Ollama modeli listelemek için `2.1 saniye` harcadığından, backend'in yanıt vermesi `4.2 saniye` sürüyor ve arayüz 3. saniyede bağlantıyı kesip hata veriyordu.
* **Çözüm:** 
  1. Backend API sağlık kontrolü optimize edildi; Ollama'ya yapılan mükerrer istekler teke indirilerek `timeout=2.5` sınırı konuldu.
  2. Streamlit arayüzündeki sağlık kontrolü zaman aşımı süresi yerel donanım gecikmelerini tolere etmek için **10 saniyeye** çıkarıldı.

---

## 📂 4. Proje Dizin Yapısı ve Dosya Rehberi

Projenin kaynak kodları masaüstündeki [aegis-rag](file:///C:/Users/mete_/Desktop/aegis-rag) klasöründedir:

* **[run_aegis.bat](file:///C:/Users/mete_/Desktop/aegis-rag/run_aegis.bat)**: Sistemi (Ollama, API ve UI) tek tıkla başlatan Windows batch betiği.
* **[src/ui/app.py](file:///C:/Users/mete_/Desktop/aegis-rag/src/ui/app.py)**: Streamlit premium koyu temalı kullanıcı arayüzü. İlerleme çubuğu, anlamsal ısı haritası ve karar günlüğü (Tree Trace) panellerini yönetir.
* **[src/backend/main.py](file:///C:/Users/mete_/Desktop/aegis-rag/src/backend/main.py)**: FastAPI sunucusu. Deterministik yönlendirme, bağlam ekonomisi ve NLI doğrulama katmanlarını barındırır.
* **[src/parser/toc_extractor.py](file:///C:/Users/mete_/Desktop/aegis-rag/src/parser/toc_extractor.py)**: Gelişmiş layout-aware PDF/Markdown okuyucu ve TOC hiyerarşi çıkarıcı.
* **[src/database/db_manager.py](file:///C:/Users/mete_/Desktop/aegis-rag/src/database/db_manager.py)**: SQLite (Graf ilişkileri ve düğüm çıpaları) ile ChromaDB (embedding arama) entegrasyon yöneticisi.
* **[tests/benchmark_suite.py](file:///C:/Users/mete_/Desktop/aegis-rag/tests/benchmark_suite.py)**: Sistemin her katmanını milisaniyeler bazında izole test eden ve `aegis_benchmark_report.md` üreten test modülü.
* **[db/](file:///C:/Users/mete_/Desktop/aegis-rag/db/)**: Yerel SQLite veritabanı (`aegis_rag.db`) ve ChromaDB vektör dizininin tutulduğu klasör.

---

## 🚀 5. Doğrulama ve Çalıştırma

### Sistemi Başlatma
Masaüstündeki **`run_aegis.bat`** dosyasını çift tıklayarak çalıştırabilirsiniz. Bu dosya sırasıyla:
1. Ollama servisinin çalışıp çalışmadığını kontrol eder (çalışmıyorsa başlatır).
2. FastAPI backend sunucusunu `127.0.0.1:8002` adresinde başlatır.
3. Streamlit arayüzünü `http://localhost:8501` adresinde ayağa kaldırır.

### Performans Analizi (Benchmark)
Tüm sistem bileşenlerinin hız bütçesini, arama isabetini ve sıkıştırma kalitesini ölçmek için masaüstü dizininde terminalden şu komutu çalıştırabilirsiniz:
```powershell
python tests/benchmark_suite.py
```
Bu komut, yerel donanımda izole testleri koşturacak ve performans raporunu güncelleyecektir.

---

## 🧬 6. Mimari Evrim Planı v2: Local PageIndex + RAG Füzyonu

> **Durum:** Tasarım (2026-06-14). Henüz uygulanmadı. Bu bölüm, projenin yön değişimini ve hedef mimariyi tanımlar. Mevcut kod (`main.py` routing + ChromaDB fallback) bu mimarinin kabasına sahiptir; bu bir **sıfırdan yazım değil, refactor**'dur.

### 6.1. Vizyon ve Boşluk Analizi

**Amaç:** İki teknolojinin güçlü yanlarını **tam yerel** ve **min 16GB RAM** kısıtında birleştirmek:

| Teknoloji | Güçlü yanı | Zayıf yanı (bizim çözdüğümüz) |
|---|---|---|
| **PageIndex** ([repo](https://github.com/VectifyAI/PageIndex)) | Hiyerarşik ağaç + akıl yürütme tabanlı navigasyon ("similarity ≠ relevance"), izlenebilir alıntılar | **Tam local değil** — LLM API key gerektirir (varsayılan GPT-4o). "No Vector DB" katı duruşu. |
| **Açık kaynak RAG** | Tam local, ucuz, ince taneli pasaj bulma | Düz chunk + saf benzerlik; yapı/bağlam kaybı |
| **Chandra** ([repo](https://github.com/datalab-to/chandra)) | Layout/tablo/matematik/el yazısı duyarlı yüksek kalite parse | **9B VLM, ~18GB BF16, resmî H100 önerisi.** Quantized GGUF var ama GPU'suz çok yavaş. |

**Sonuç:** "Local PageIndex" gerçek bir boşluktur. Çözümümüz = ağacın akıl yürütmesi + vektörün ince aramasını **iki aşamalı** birleştirmek; ağır parser'ı **opsiyonel** tutmak.

### 6.2. Temel Tasarım Kararları

1. **Katmanlı (pluggable) parser** — tek bir modele kilitlenme:
   - **Hızlı kat (varsayılan):** PyMuPDF (mevcut) — dijital/temiz PDF, anında.
   - **İyi kat:** Docling / Marker — yapı+tablo kalitesi yüksek, local, makul ağırlık.
   - **En iyi kat (opsiyonel):** Chandra — yalnız taranmış/el yazısı/karmaşık tablo-matematik için; local quantized **veya** Datalab API seçeneğiyle. **Zorunlu değil** → "16GB'da çalışır" vaadi korunur.

2. **İki aşamalı retrieval** (eksen = *granülerlik*, derinlik değil):
   - **Ağaç = kaba navigasyon** → doğru bölümü/alt-ağacı seçer (PageIndex gücü).
   - **Vektör = seçilen yaprağın *içinde* ince arama** → tam pasajı bulur (RAG gücü).
   - Vektör artık "ağaç başarısız olursa fallback" değil, **seçilen kapsam içindeki ikinci aşama**.

3. **Derinlik sınırı (5-6)** bir runtime switch değil, **ağaç inşa parametresi**: daha derin yapı yaprak içeriğine "katlanır" ki her yaprak makul boyutta kalsın.

4. **LLM düğüm özetleri** — indekslemede her düğüme kısa LLM özeti (mevcut "ilk 3 cümle" naif yöntemin yerine). Ağaç navigasyon kalitesi buna bağlıdır.

5. **Recursive descent navigasyon** — tüm düz ağacı tek prompt'a basmak yerine (büyük belgede 7B'yi boğar) seviye seviye in. Her LLM çağrısı küçük kalır → 16GB+7B kısıtına doğrudan hizmet eder.

6. **Alıntı tabanlı grounding** — Self-NLI yerine cevapta zorunlu sayfa/bölüm alıntısı (PageIndex "traceable" felsefesi; daha ucuz ve güçlü).

### 6.3. Çıkarılacaklar (basitleştir + 16GB'a yer aç)

- **Self-NLI** (aynı modelle kendini denetleme): LLM çağrısını ikiye katlıyor, zayıf → alıntı grounding ile değiştir.
- **Anahtar kelime tabanlı execution policy** (kırılgan Türkçe liste): kaldır, navigasyon doğal karar versin.
- **Regex DAG linkleri**: gürültülü, düşük değer → şimdilik çıkar.
- **Her sorguda iki hattı birden koşmak**: israf → ağaç-önce, vektör-yaprak-içinde.

### 6.4. Hedef Boru Hattı

```
İNDEKSLEME (offline, tek seferlik):
  Stage 0  Parse  → katmanlı (PyMuPDF / Docling / Chandra) → yapısal Markdown
  Stage 1  Ağaç   → hiyerarşi çıkar, derinlik 5-6'da katla, her düğüme LLM özeti
  Stage 2  Embed  → SADECE yaprak pasajlarını vektörle (kapsam dar → küçük DB, hızlı)

SORGU (online):
  1. Recursive descent ile ağaçta gez → ilgili yaprak/alt-ağaç(lar)ı seç
  2. O kapsam İÇİNDE dense + lexical retrieval → tam pasaj
  3. Token bütçesiyle sentez + zorunlu alıntı (sayfa/bölüm)

MODEL YAŞAM DÖNGÜSÜ (16GB):
  parse → unload → embed → query. Asla aynı anda iki ağır model yok.
  qwen2.5:7b Q4 (~4.7GB) + nomic-embed (~0.3GB) → 16GB'a rahat sığar.
  Chandra (opsiyonel) yalnız parse fazında, sonra unload.
```

### 6.5. Önce Yapılacak Retrieval Düzeltmeleri (mevcut koda)

Mimariye geçmeden mevcut retrieval'ı sağlamlaştıran hızlı kazanımlar (detay: hafıza `project-roadmap`):
1. ChromaDB `hnsw:space="cosine"` + doğru `similarity` formülü.
2. Embedding fallback'i fail-fast yap + timeout artır.
3. nomic task prefix'leri (`search_query:` / `search_document:`).

### 6.6. Açık Sorular / İleride Karar Verilecek

- Docling mi Marker mı "iyi kat" olsun? (kalite/ağırlık kıyası gerekli)
- Çoklu belge üzerinde cross-document sorgu kapsama dahil mi?
- Ağaç özetlerini hangi modelle üretelim (qwen2.5:7b yeterli mi, yoksa daha küçük hızlı bir model mi)?
- Recursive descent'te "yanlış dala saparsa" geri dönüş (backtrack) stratejisi.
