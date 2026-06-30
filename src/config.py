"""
Aegis RAG merkezi yapılandırması.

Tüm sabitler burada toplanır; mimari model-agnostiktir (Qwen/Llama/Gemma fark etmez,
tek satır değişir). Değerler `AEGIS_*` ortam değişkenleriyle ezilebilir.
"""
import os


# --- Servis adresleri ---
# localhost yerine kararlı IPv4 (IPv6 çakışması geçmişi — bkz. project_brain.md Hata 1).
OLLAMA_URL = os.getenv("AEGIS_OLLAMA_URL", "http://127.0.0.1:11434")
BACKEND_HOST = os.getenv("AEGIS_BACKEND_HOST", "127.0.0.1")
BACKEND_PORT = int(os.getenv("AEGIS_BACKEND_PORT", "8002"))
BACKEND_URL = f"http://{BACKEND_HOST}:{BACKEND_PORT}"

# --- Modeller ---
LLM_MODEL = os.getenv("AEGIS_LLM_MODEL", "qwen3:8b")            # navigasyon + sentez
SUMMARY_MODEL = os.getenv("AEGIS_SUMMARY_MODEL", "qwen3:8b")    # düğüm özetleri (offline)
EMBED_MODEL = os.getenv("AEGIS_EMBED_MODEL", "bge-m3")  # çok dilli/Türkçe güçlü; alt: nomic-embed-text

# Parser katmanı: "auto" (gömülü TOC → Docling varsa → PyMuPDF), "pymupdf" (hızlı,
# bağımlılıksız), "docling" (layout-model, alt-bölüm çözünürlüğü; opsiyonel paket),
# "chandra" (taranmış/el-yazısı; opsiyonel). Docling/Chandra yoksa otomatik PyMuPDF'e düşer.
PARSER = os.getenv("AEGIS_PARSER", "auto")

# Düğüm özetleri: True ise indekslemede her düğüm için LLM özeti üretilir (navigasyon
# kalitesini artırır ama indekslemeyi yavaşlatır). False ise hızlı çıkarımsal (ilk N cümle)
# özet kullanılır. LLM çağrısı başarısız olursa otomatik çıkarımsal özete düşülür.
USE_LLM_SUMMARIES = os.getenv("AEGIS_USE_LLM_SUMMARIES", "true").lower() == "true"

# Thinking modelleri (qwen3 vb.) düşünce çıktısını JSON/sentez yanıtına sızdırmasın.
LLM_THINKING = False

# --- ChromaDB / Retrieval ---
CHROMA_COLLECTION = "aegis_rag_chunks"
SEMANTIC_TOP_K = 10        # ChromaDB ham aday sayısı (global heatmap)
SCOPED_TOP_K = 30          # yaprak-içi/kapsam-içi arama ham aday (büyük yapraklarda recall)
RERANK_TOP_K = 5           # heatmap için tutulan düğüm sayısı
CONTEXT_PASSAGES = 10      # sentezе beslenen pasaj sayısı (hız/recall dengesi; 12→10)
SUMMARY_WORKERS = 4        # indekslemede özetleri paralel üret (bounded thread havuzu)
RERANK_COSINE_WEIGHT = 0.6
RERANK_LEXICAL_WEIGHT = 0.4

# --- Bağlam ekonomisi ---
TOKEN_BUDGET = 4000
TOKEN_PER_WORD = 1.35      # Türkçe yaklaşık token/kelime oranı

# --- Chunking (embedding birimleri) ---
# PDF satır-kırılması ham metni ~90 karakterlik parçalara böler ve cümleleri/bilgileri
# parçalar (kesin bilgi retrieve edilemez). Cümle-duyarlı + örtüşmeli sabit-boy chunk.
CHUNK_SIZE_CHARS = 700
CHUNK_OVERLAP_CHARS = 150

# --- Ağaç navigasyonu ---
MAX_TREE_DEPTH = 6
MAX_LEAVES = 3
MAX_DESCENT_CALLS = 8
# Düz/küçük ağaçta (bu eşiğin altında düğüm) recursive descent atlanır; doğrudan
# ısı-kapsamlı aramaya geçilir (boşa LLM çağrısını önler — model-agnostik hız).
DESCENT_MIN_NODES = 25

# --- Zaman aşımları (saniye) ---
OLLAMA_HEALTH_TIMEOUT = 2.5
OLLAMA_GENERATE_TIMEOUT = 60
EMBED_TIMEOUT = 30

# Modelleri RAM'de sıcak tut (bge-m3 ↔ qwen3 takası/yeniden-yükleme thrashing'ini azaltır).
# 16GB'da iki model birlikte sığmazsa Ollama yine takas eder; o durumda kısalt veya -1 dene.
OLLAMA_KEEP_ALIVE = os.getenv("AEGIS_OLLAMA_KEEP_ALIVE", "30m")
