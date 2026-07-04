# FAZ 5 — Aegis Engine: Parser- ve Model-Agnostik Belge Sohbet Motoru

> **Bu belge, uygulayıcı agent (Opus) için kendi kendine yeterli bir iş planıdır.**
> Mevcut mimariyi anlamak için önce `CLAUDE.md` ve `project_brain.md` (§6-7) okunmalıdır.
> Çalışma dizini proje köküdür; testler `python -m unittest discover tests` ile koşar (26 test, hepsi geçiyor olmalı).

---

## 0. Vizyon (ürünün tanımı)

Şirketler klasik RAG'dan verim alamıyor çünkü RAG **bilgi hiyerarşisini bilmiyor**. Aegis'in çözümü kanıtlandı: belgeden hiyerarşik ağaç çıkar (PageIndex mantığı), başlık içeriklerini vektör uzayında sakla, sorguda ağaçla NEREDE'yi, vektörle TAM CÜMLE'yi bul (çift-hat).

**Faz 5'in hedefi:** Bu kanıtlanmış çekirdeği bir **motora** dönüştürmek:

1. **Parser-agnostik** — şirket kendi parser'ını yazar (`BaseParser` sözleşmesi), motor gerisini halleder.
2. **Model-agnostik** — şirket istediği local LLM/embedder'ı takar (Ollama veya OpenAI-uyumlu local endpoint: vLLM, LM Studio, llama.cpp server). Zayıf modelle bile sistem çalışır (deterministik yedek merdiveni), güçlü modelle uçar.
3. **Ölçülebilir** — şirket kendi belgeleri + kendi bileşenleriyle "Aegis vs saf RAG" farkını tek komutla ölçebilir (ablation raporu). Satış argümanı budur.

**Değişmezler (dokunma):** ağaç veri modeli (`toc_nodes` + `node_id` köprüsü), recursive descent + çift-hat kapsam kesişimi + token bütçesi orkestrasyonu, cümle-duyarlı chunking, alıntılı sentez. Bunlar motorun kendisi. "Graph/DAG" fikri bilinçli olarak kapsam dışıdır (tree mantığı korunur).

---

## 1. Hedef mimari (katmanlar)

```
┌─────────────────────────────────────────────────────────┐
│  API / UI (FastAPI + Streamlit — referans istemci)       │
├─────────────────────────────────────────────────────────┤
│  AEGIS CORE ENGINE (değişmez çekirdek)                    │
│  • IR normalize → finalize_tree (depth fold, is_leaf)     │
│  • Index orchestration (özet + chunk + embed + store)     │
│  • Query orchestration (heatmap → descent → çift-hat      │
│    kapsam → scoped retrieval → bütçe → alıntılı sentez)   │
│  • Degradation ladder (her LLM adımının deterministik     │
│    yedeği)                                                │
├──────────────┬──────────────────┬───────────────────────┤
│ ParserRegistry│  LLMProvider     │  EmbedderProvider      │
│ (BaseParser)  │  (BaseLLM)       │  (BaseEmbedder)        │
├──────────────┼──────────────────┼───────────────────────┤
│ pymupdf       │ ollama           │ ollama (bge-m3, nomic) │
│ docling       │ openai-uyumlu    │ openai-uyumlu          │
│ chandra(ops.) │ (vLLM/LM Studio) │                        │
│ ŞİRKETİN KENDİ│ ŞİRKETİN KENDİ   │ ŞİRKETİN KENDİ         │
└──────────────┴──────────────────┴───────────────────────┘
         Depo: SQLite (ağaç) + ChromaDB (vektör) — mevcut
```

Anahtar ilke: **Core Engine hiçbir somut parser/model adı bilmez.** Yalnız sözleşmeleri bilir.

---

## 2. Sözleşmeler (yeni dosyalar)

### 2.1 `src/contracts/parser.py`
```python
@dataclass
class RawNode:
    """Her parser'ın üretmek ZORUNDA olduğu ara temsil (IR). Motorun tek girdisi."""
    heading: str
    level: int                      # 1 = en üst
    content: str                    # bu başlığın altındaki ham metin
    start_page: Optional[int] = None
    end_page: Optional[int] = None

class BaseParser(ABC):
    name: str                       # registry anahtarı ("pymupdf", "docling", "acme_custom")
    @abstractmethod
    def parse(self, file_path: str, progress_cb=None) -> list[RawNode]: ...
    def supports(self, file_path: str) -> bool:   # uzantı/format kontrolü
        return file_path.lower().endswith(".pdf")
```
- `src/parser/toc_extractor.py`'deki mevcut üç yol (bookmark / docling / pymupdf-sezgisel) bu arayüzün **üç somut adaptörü** olarak yeniden paketlenir (mantık AYNEN taşınır, yeniden yazılmaz).
- `ParserRegistry`: isim → sınıf; `config.PARSER="auto"` sıralı dener (bookmark → docling → pymupdf), `"acme_custom"` gibi harici sınıf yolu da (`"paket.modul:Sinif"`) import edebilmeli. **Şirket parser'ı tek dosya yazıp config'e adını koyar — motor kodu değişmez.**
- Motor tarafı: `RawNode` listesi → mevcut `finalize_tree` (id/parent/path üretimi + depth fold + is_leaf + gürültü filtresi). `finalize_tree` girişini RawNode'a uyarlamak gerekir (id/parent'ı level dizisinden stack ile motor kurar — `extract_toc_tree`'deki mevcut stack mantığı ortak yardımcıya çekilir).

### 2.2 `src/contracts/llm.py`
```python
class BaseLLM(ABC):
    @abstractmethod
    def generate(self, prompt: str, *, temperature=None, json_mode=False) -> str: ...
    @abstractmethod
    def generate_stream(self, prompt: str) -> Iterator[str]: ...
```
- `OllamaLLM` (mevcut `ollama_generate` + stream kodu buraya taşınır; `think` parametresi Ollama-özel detay olarak adaptör içinde kalır).
- `OpenAICompatLLM`: `base_url + model` ile herhangi bir OpenAI-uyumlu local sunucu (vLLM, LM Studio, llama.cpp). Sadece `requests` ile `/v1/chat/completions` — ekstra SDK bağımlılığı YOK.
- Config: `LLM_PROVIDER=ollama|openai_compat`, `LLM_BASE_URL`, `LLM_MODEL`.

### 2.3 `src/contracts/embedder.py`
- Mevcut `OllamaEmbedder` → `BaseEmbedder(embed_documents, embed_query)` arayüzüne uyarlanır + `OpenAICompatEmbedder` (`/v1/embeddings`).
- Koleksiyon metadata'sındaki `embed_model` mantığı korunur (model değişince otomatik yeniden kurulum) — provider+model birleşik anahtar olur.

**Kabul kriteri (Faz 5.1):** Mevcut 26 test geçer + yeni sözleşme testleri; `config` üzerinden pymupdf/docling seçimi ve sahte (fake) bir custom parser'ın uçtan uca indekslenebildiğini gösteren test; davranış birebir korunur (tez: docling ile 52 düğüm).

---

## 3. Zayıf-model dayanıklılığı — "sistem gücü" iddiasının teknik karşılığı (Faz 5.2)

Şirket 3B'lik model takarsa bile sistem çalışmalı. Her LLM adımı için **merdiven**:

| Adım | 1. deneme | 2. deneme | Deterministik yedek |
|---|---|---|---|
| Descent | JSON iste | parse hatasında 1 kez "SADECE JSON döndür, açıklama yazma" onarım çağrısı | ısı-kapsamlı arama (mevcut) |
| Özet | LLM özeti | — | çıkarımsal ilk-N cümle (mevcut) |
| Sentez | alıntılı sentez | — | en iyi 3 pasajı "Belgede bulunanlar:" başlığıyla ham sun (yeni: `EXTRACTIVE_ANSWER` modu — model tamamen çökerse bile kullanıcı pasajları alır) |

Ek işler:
- **Adaptif descent prompt'u**: frontier > 15 düğümse özetleri 200→100 karaktere kısalt; `confidence` alanını zayıf modeller dolduramıyorsa yokluğunu MEDIUM say (şu an sayılıyor — koru).
- **JSON onarımı** `clean_json_response`'a: ilk `{...}` bloğunu regex ile çek (kod bloğu yoksa da), trailing-comma temizle.
- **Model sağlık kontrolü** `/api/health`'e: seçili LLM+embedder'a 1'er mini çağrı, gecikmeleriyle raporla ("kurulum doğru mu" teşhisi için).

**Kabul kriteri:** `tests/test_resilience.py` — sahte LLM (bozuk JSON döndüren / hep hata fırlatan) ile query_aegis'in yine anlamlı yanıt (extractive dahil) döndürdüğünü test eder.

---

## 4. Model-bağımsız retrieval tabanı (Faz 5.3)

Motorun kalitesi modele değil kendine dayansın diye **model-gerektirmeyen** iyileştirmeler:

1. **BM25 + Türkçe-dostu eşleme**: `lexical_semantic_rerank`'teki saf kesişim yerine BM25 (IDF'li; saf Python, bağımlılıksız — chunk sayısı küçük). Token eşleme **karakter 3-gram Jaccard** ile yumuşatılır (eklemeli dillerde "arşivinden"≈"arşiv"; stemmer bağımlılığı istemiyoruz, dil-bağımsız kalır).
2. **Heatmap yoğunlaştırma**: heatmap `SCOPED_TOP_K`(30) chunk'tan kurulur (şu an 10); düğüm skoru = `max_chunk_skoru * (1 + 0.1*log2(eşleşen_chunk_sayısı))` — yoğun eşleşen bölüm tek şanslı chunk'tan ayrışır.
3. **Chunk-düzeyi sayfa atfı**: `_chunk_text` reflow'u `<!-- Page N -->` işaretlerini korusun; her chunk kendi gerçek sayfasını metadata'ya yazsın (şu an düğümün start_page'i — yaklaşık). Alıntılar gerçek sayfa olur.
4. **Tablo bütünlüğü**: chunker markdown tablo bloklarını (`|` ile başlayan ardışık satırlar) bölünmez birim saysın; tablo `CHUNK_SIZE_CHARS`'ı aşarsa başlık satırı her parçada tekrarlansın.

**Kabul kriteri:** birim testler (BM25 IDF sıralaması; 3-gram "arşivinden/arşiv" eşleşmesi; tablo bölünmezliği; chunk sayfa doğruluğu) + tez golden-set'inde regresyon yok.

---

## 5. Değerlendirme kiti — satış argümanının kanıtı (Faz 5.4)

`tests/benchmark_suite.py` genişletilir → **`aegis-bench`**:

1. Golden-set şemasına `expected_node_ids` (isteğe bağlı) eklenir → **üç metrik**: cevap doğruluğu (mevcut expect/forbid), retrieval isabeti (beklenen düğümden pasaj geldi mi), routing isabeti (descent hedefi doğru mu).
2. **Ablation modu**: aynı seti iki kez koşar — (a) tam motor, (b) `AEGIS_ABLATION=flat_rag` (descent+ağaç kapalı, yalnız global vektör top-k → sentez). Rapor: "Aegis vs saf RAG: doğruluk +X, isabet +Y". **Şirketin kendi belgesi + kendi modeliyle bu raporu üretebilmesi ürünün satış kanıtıdır.**
3. Rapora ortam bilgisi: parser adı, LLM, embedder, gecikme dağılımı (p50/p95).

**Kabul kriteri:** `python tests/benchmark_suite.py --ablation` iki modu koşup karşılaştırmalı Markdown raporu üretir.

---

## 6. Çok-belge + sohbet (Faz 5.5)

1. **Soru yoğunlaştırma**: `/api/query`'ye opsiyonel `history: [{role, content}]`; varsa tek ucuz LLM çağrısıyla bağımsız soruya çevrilir ("Peki o ne işe yarar?" → "Guardrail ne işe yarar?"). Yedek: history yoksa/çağrı patlarsa ham soru.
2. **Çapraz-belge routing**: `document_id` yerine opsiyonel `document_ids: [..]`. Descent'in sıfırıncı seviyesi belge seçimi olur: her belgenin kök özeti frontier gibi sunulur, LLM ilgili belge(ler)i seçer, sonra normal descent. Heatmap zaten `where document_id ∈ {...}` ile çoklu çalışabilir (Chroma `$in`).
3. UI: belge çoklu seçim + sohbet geçmişinin history olarak gönderilmesi.

**Kabul kriteri:** iki belge yüklüyken "X belgesindeki A ile Y belgesindeki B'yi karşılaştır" sorusu her iki belgeden pasajla yanıtlanır; takip sorusu bağlam korur.

---

## 7. Servisleşme + sertleştirme (Faz 5.6)

1. **Async**: endpoint'ler `async def` + `httpx.AsyncClient` (Ollama/OpenAI çağrıları); SQLite erişimi mevcut `_write_lock` + kısa işlemler ile kalabilir (aiosqlite'a geçiş opsiyonel).
2. **Güvenlik**: upload dosya adı `os.path.basename` + `uuid4` öneki (path traversal + çakışma); CORS varsayılanı `http://localhost:8501`; `MAX_UPLOAD_MB` sınırı.
3. **`UPLOAD_STATUS`**: TTL'li temizlik (bitmiş job'lar 1 saat sonra düşer).
4. **Paketleme**: `pyproject.toml` ile `aegis-rag` pip paketi; `aegis serve` (API), `aegis index <dosya>`, `aegis bench` konsol komutları. Docling/Chandra `extras` olarak: `pip install aegis-rag[docling]`.
5. **Şirket entegrasyon dokümanı** `docs/INTEGRATION.md`: "Kendi parser'ını 30 satırda yaz", "vLLM'ini bağla", "benchmark'ını koş" üç kısa rehber.

**Kabul kriteri:** temiz venv'de `pip install -e . && aegis serve` çalışır; path-traversal testi geçer; 2 eşzamanlı sorgu birbirini bloklamaz (async doğrulaması).

---

## 8. Uygulama sırası ve bağımlılıklar

| Sıra | Faz | Neden bu sırada |
|---|---|---|
| 1 | **5.1 Sözleşmeler** | Her şeyin temeli; saf refactor, davranış korunur |
| 2 | **5.3 Retrieval tabanı** | Model-bağımsız kalite; 5.4'ün ölçeceği şey |
| 3 | **5.2 Dayanıklılık** | Zayıf-model iddiası; 5.1'in LLM arayüzüne dayanır |
| 4 | **5.4 Değerlendirme kiti** | Kanıt üretir; sonraki her değişikliğin bekçisi |
| 5 | **5.5 Çok-belge/sohbet** | Ürün özelliği; çekirdek stabilken |
| 6 | **5.6 Servisleşme** | En son cila/paketleme |

**Genel kurallar (uygulayıcı için):**
- Her faz sonunda: tüm testler geçer + `git commit` (Türkçe mesaj, **Co-Authored-By trailer'ı KOYMA**) + `CLAUDE.md`/`project_brain.md` güncellenir.
- Mevcut davranışı bozan hiçbir değişiklik testsiz girmez; şüphede tez PDF'i (`C:/Users/mete_/Desktop/Flight-risk-thesis.pdf`) ile `tools/compare_parsers.py` ve golden-set koşulur.
- Sabitler `src/config.py`'ye, `AEGIS_*` env override desteğiyle.
- Ollama'ya doğrudan `requests` çağrısı yalnız adaptörlerde kalır; core engine yalnız sözleşme arayüzlerini görür.
