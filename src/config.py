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
EMBED_MODEL = os.getenv("AEGIS_EMBED_MODEL", "nomic-embed-text")  # alternatif: bge-m3

# Düğüm özetleri: True ise indekslemede her düğüm için LLM özeti üretilir (navigasyon
# kalitesini artırır ama indekslemeyi yavaşlatır). False ise hızlı çıkarımsal (ilk N cümle)
# özet kullanılır. LLM çağrısı başarısız olursa otomatik çıkarımsal özete düşülür.
USE_LLM_SUMMARIES = os.getenv("AEGIS_USE_LLM_SUMMARIES", "false").lower() == "true"

# Thinking modelleri (qwen3 vb.) düşünce çıktısını JSON/sentez yanıtına sızdırmasın.
LLM_THINKING = False

# --- ChromaDB / Retrieval ---
CHROMA_COLLECTION = "aegis_rag_chunks"
SEMANTIC_TOP_K = 10        # ChromaDB ham aday sayısı
RERANK_TOP_K = 5           # rerank sonrası tutulan aday
RERANK_COSINE_WEIGHT = 0.6
RERANK_LEXICAL_WEIGHT = 0.4

# --- Bağlam ekonomisi ---
TOKEN_BUDGET = 4000
TOKEN_PER_WORD = 1.35      # Türkçe yaklaşık token/kelime oranı

# --- Ağaç navigasyonu (Faz 2-3'te kullanılacak) ---
MAX_TREE_DEPTH = 6
MAX_LEAVES = 3
MAX_DESCENT_CALLS = 8

# --- Zaman aşımları (saniye) ---
OLLAMA_HEALTH_TIMEOUT = 2.5
OLLAMA_GENERATE_TIMEOUT = 60
EMBED_TIMEOUT = 30
