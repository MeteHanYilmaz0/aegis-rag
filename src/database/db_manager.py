import sqlite3
import os
import re
import threading
import requests
from typing import List, Dict, Any, Optional
import chromadb

from src import config

# Projenin kök dizininde db klasörü oluşturma
DB_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "db"))
os.makedirs(DB_DIR, exist_ok=True)

SQLITE_PATH = os.path.join(DB_DIR, "aegis_rag.db")
CHROMA_PATH = os.path.join(DB_DIR, "chroma_db")

# Şema sürümü. Değiştiğinde (PRAGMA user_version uyuşmazsa) tablolar düşürülüp yeniden
# kurulur ve belgelerin yeniden indekslenmesi gerekir (dev verisi).
SCHEMA_VERSION = 2

class OllamaEmbedder:
    """
    Ollama yerel embedding modeli (varsayılan: nomic-embed-text).

    FAIL-FAST: Ollama/model erişilemezse SESSİZCE başka bir modele yedeklenmez.
    Sessiz yedekleme, farklı boyutlu/uzaylı vektörlerin aynı koleksiyona karışmasına
    ve arama sonuçlarının anlamsızlaşmasına yol açar. Bunun yerine açık hata fırlatır.

    Görev ön-ekleri (task prefix): nomic-embed-text, dokümanlar için "search_document:",
    sorgular için "search_query:" ön-eki bekler; bu ön-ekler retrieval kalitesini belirgin
    artırır. Ön-ek gerektirmeyen modellerde (bge-m3 vb.) boş bırakılır.
    """

    def __init__(self, model: str = config.EMBED_MODEL, base_url: str = config.OLLAMA_URL):
        self.model = model
        self.base_url = base_url
        uses_prefix = model.startswith("nomic")
        self.doc_prefix = "search_document: " if uses_prefix else ""
        self.query_prefix = "search_query: " if uses_prefix else ""

    def _embed_one(self, text: str) -> List[float]:
        response = requests.post(
            f"{self.base_url}/api/embeddings",
            json={"model": self.model, "prompt": text},
            timeout=config.EMBED_TIMEOUT,
        )
        if response.status_code != 200:
            raise RuntimeError(
                f"Embedding modeli '{self.model}' yanıt vermedi (HTTP {response.status_code}). "
                f"Ollama çalışıyor mu ve `ollama pull {self.model}` yapıldı mı kontrol edin."
            )
        return response.json()["embedding"]

    def embed_documents(self, texts: List[str]) -> List[List[float]]:
        return [self._embed_one(self.doc_prefix + t) for t in texts]

    def embed_query(self, text: str) -> List[float]:
        return self._embed_one(self.query_prefix + text)


class DBManager:
    """
    SQLite ve ChromaDB veri tabanlarını koordine eden gelişmiş veri erişim katmanı.
    DAG bağlantıları, Çoklu Temsilcili Düğüm Çıpaları ve çapraz referansları yönetir.
    """
    def __init__(self):
        self.sqlite_conn = sqlite3.connect(SQLITE_PATH, check_same_thread=False)
        self.sqlite_conn.row_factory = sqlite3.Row
        # Tek paylaşılan bağlantı çok thread'de kullanıldığından yazmaları seri hale getir.
        self._write_lock = threading.Lock()
        self._init_sqlite()
        
        # ChromaDB yerel istemcisini başlat. Embedding'leri Chroma'ya elle veriyoruz
        # (query/doc ön-ekleri için), bu yüzden koleksiyona embedding_function bağlamıyoruz.
        self.chroma_client = chromadb.PersistentClient(path=CHROMA_PATH)
        self.embedder = OllamaEmbedder()
        self.chroma_collection = self._get_cosine_collection()

    def _get_cosine_collection(self):
        """
        Cosine mesafe uzaylı koleksiyonu döndürür.

        ÖNEMLİ: ChromaDB varsayılanı L2'dir; L2 ile `similarity = 1 - distance` formülü
        yanlış sonuç verir (mesafe 1'i aşar, benzerlik negatife/0'a kırpılır). Doğru
        kosinüs benzerliği için koleksiyon `hnsw:space=cosine` ile oluşturulmalıdır.
        Eski (L2) bir koleksiyon bulunursa, doğru benzerlik için yeniden oluşturulur —
        bu durumda belgelerin yeniden indekslenmesi gerekir (dev verisi).
        """
        name = config.CHROMA_COLLECTION
        col = self.chroma_client.get_or_create_collection(
            name=name, metadata={"hnsw:space": "cosine"}
        )
        if (col.metadata or {}).get("hnsw:space") != "cosine":
            self.chroma_client.delete_collection(name)
            col = self.chroma_client.get_or_create_collection(
                name=name, metadata={"hnsw:space": "cosine"}
            )
        return col

    def _init_sqlite(self):
        """SQLite tablolarını ilklendirir (WAL + şema sürüm göçü)."""
        cursor = self.sqlite_conn.cursor()
        # Eşzamanlı okuma/yazma dayanıklılığı için WAL.
        cursor.execute("PRAGMA journal_mode=WAL")

        # Şema sürüm kontrolü: uyuşmuyorsa eski tabloları düşür (dev verisi yeniden indekslenir).
        current_version = cursor.execute("PRAGMA user_version").fetchone()[0]
        if current_version != SCHEMA_VERSION:
            cursor.execute("DROP TABLE IF EXISTS toc_links")
            cursor.execute("DROP TABLE IF EXISTS toc_nodes")
            cursor.execute("DROP TABLE IF EXISTS documents")

        # 1. Doküman tablosu
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS documents (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                filename TEXT NOT NULL,
                file_path TEXT NOT NULL,
                upload_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # 2. Hiyerarşik TOC düğümleri. İç düğümler navigasyon tabelası (summary),
        # yapraklar/içerik taşıyan düğümler vektörlenir. (bkz. project_brain.md §6.7)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS toc_nodes (
                id INTEGER PRIMARY KEY,
                document_id INTEGER NOT NULL,
                node_id INTEGER NOT NULL,
                heading TEXT NOT NULL,
                level INTEGER NOT NULL,
                parent_id INTEGER,
                path TEXT NOT NULL,
                is_leaf INTEGER NOT NULL DEFAULT 1,
                content TEXT,
                summary TEXT,
                start_page INTEGER,
                end_page INTEGER,
                token_count INTEGER,
                start_line INTEGER,
                end_line INTEGER,
                FOREIGN KEY (document_id) REFERENCES documents (id) ON DELETE CASCADE
            )
        """)
        # Recursive descent'in çocuk getirmesi için indeks.
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_nodes_parent ON toc_nodes(document_id, parent_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_nodes_doc ON toc_nodes(document_id, node_id)")

        cursor.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        self.sqlite_conn.commit()

    # ------------------------------------------------------------------ #
    #  Özet üretimi
    # ------------------------------------------------------------------ #
    def _extractive_summary(self, content: str, heading: str) -> str:
        """Hızlı çıkarımsal özet: ilk birkaç cümle (LLM yoksa/kapalıyken)."""
        content = (content or "").strip()
        if not content:
            return heading
        sentences = re.split(r'(?<=[.!?])\s+', content)
        return " ".join(sentences[:3]) if sentences else heading

    def _llm_summary(self, content: str, heading: str) -> str:
        """LLM ile tek cümlelik nesnel özet. Hata olursa istisna fırlatır (çağıran yedekler)."""
        prompt = (
            "Aşağıdaki bölümü tek cümlede, Türkçe ve nesnel biçimde özetle. "
            "Sadece özeti yaz, başka açıklama ekleme.\n\n"
            f"BAŞLIK: {heading}\n\nMETİN:\n{content[:2000]}"
        )
        response = requests.post(
            f"{config.OLLAMA_URL}/api/generate",
            json={"model": config.SUMMARY_MODEL, "prompt": prompt,
                  "stream": False, "think": config.LLM_THINKING},
            timeout=config.OLLAMA_GENERATE_TIMEOUT,
        )
        response.raise_for_status()
        text = re.sub(r'<think>.*?</think>', '', response.json().get("response", ""), flags=re.DOTALL).strip()
        if not text:
            raise RuntimeError("Boş özet")
        return text

    def _make_summary(self, content: str, heading: str) -> str:
        """Düğüm özeti üretir: LLM açıksa onu dener, aksi/başarısızsa çıkarımsal özete düşer."""
        if config.USE_LLM_SUMMARIES and (content or "").strip():
            try:
                return self._llm_summary(content, heading)
            except Exception:
                pass
        return self._extractive_summary(content, heading)

    def add_document(self, filename: str, file_path: str, toc_nodes: List[Dict[str, Any]], progress_callback=None) -> int:
        """Yazma işlemini kilitle (tek paylaşılan SQLite bağlantısı çok thread'de güvenli olsun)."""
        with self._write_lock:
            return self._add_document_locked(filename, file_path, toc_nodes, progress_callback)

    def _add_document_locked(self, filename: str, file_path: str, toc_nodes: List[Dict[str, Any]], progress_callback=None) -> int:
        """
        Yeni dokümanı SQLite (hiyerarşik ağaç) + ChromaDB'ye (içerik taşıyan düğümlerin
        pasajları) kaydeder. Her düğüme özet üretilir; yalnız içerik taşıyan düğümler vektörlenir.
        """
        cursor = self.sqlite_conn.cursor()

        cursor.execute(
            "INSERT INTO documents (filename, file_path) VALUES (?, ?)",
            (filename, file_path)
        )
        document_id = cursor.lastrowid

        # SQLite'a TOC düğümlerini ekle (özet + yaprak/sayfa metadatası ile)
        for node in toc_nodes:
            content = (node.get("content") or "").strip()
            heading = node["heading"]
            summary = self._make_summary(content, heading)
            token_count = int(len(content.split()) * config.TOKEN_PER_WORD) if content else 0

            cursor.execute(
                """
                INSERT INTO toc_nodes
                (document_id, node_id, heading, level, parent_id, path, is_leaf,
                 content, summary, start_page, end_page, token_count, start_line, end_line)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    document_id,
                    node["id"],
                    heading,
                    node["level"],
                    node["parent_id"],
                    node["path"],
                    node.get("is_leaf", 1),
                    content,
                    summary,
                    node.get("start_page"),
                    node.get("end_page"),
                    token_count,
                    node.get("start_line"),
                    node.get("end_line"),
                )
            )

        self.sqlite_conn.commit()

        # ChromaDB'ye yalnız içerik taşıyan düğümlerin pasajlarını ekle.
        # node_id köprüsü + start_page (alıntı) metadata olarak taşınır.
        chroma_ids = []
        chroma_documents = []
        chroma_metadatas = []

        for node in toc_nodes:
            content = (node.get("content") or "").strip()
            if content:
                paragraphs = [p.strip() for p in content.split("\n\n") if p.strip()]
                for p_idx, para in enumerate(paragraphs):
                    chroma_ids.append(f"doc_{document_id}_node_{node['id']}_p_{p_idx}")
                    chroma_documents.append(para)
                    chroma_metadatas.append({
                        "document_id": document_id,
                        "node_id": node["id"],
                        "path": node["path"],
                        "heading": node["heading"],
                        "start_page": node.get("start_page") if node.get("start_page") is not None else -1,
                    })

        if chroma_documents:
            total_chunks = len(chroma_documents)
            batch_size = 16
            for i in range(0, total_chunks, batch_size):
                batch_ids = chroma_ids[i:i+batch_size]
                batch_docs = chroma_documents[i:i+batch_size]
                batch_metas = chroma_metadatas[i:i+batch_size]
                # Embedding'leri ön-ekli olarak elle hesapla (search_document: ...)
                batch_embs = self.embedder.embed_documents(batch_docs)
                self.chroma_collection.add(
                    ids=batch_ids,
                    documents=batch_docs,
                    metadatas=batch_metas,
                    embeddings=batch_embs
                )
                if progress_callback:
                    progress_callback(min(i + batch_size, total_chunks), total_chunks)
            
        return document_id

    def get_documents(self) -> List[Dict[str, Any]]:
        """Sistemdeki tüm dokümanları listeler."""
        cursor = self.sqlite_conn.cursor()
        cursor.execute("SELECT id, filename, file_path, upload_time FROM documents ORDER BY upload_time DESC")
        rows = cursor.fetchall()
        return [dict(row) for row in rows]

    _NODE_COLUMNS = ("node_id as id, heading, level, parent_id, path, is_leaf, "
                     "content, summary, start_page, end_page, token_count, start_line, end_line")

    def get_document_nodes(self, document_id: int) -> List[Dict[str, Any]]:
        """Bir dokümana ait tüm hiyerarşik TOC düğümlerini döndürür."""
        cursor = self.sqlite_conn.cursor()
        cursor.execute(
            f"SELECT {self._NODE_COLUMNS} FROM toc_nodes WHERE document_id = ? ORDER BY node_id ASC",
            (document_id,)
        )
        return [dict(row) for row in cursor.fetchall()]

    def get_node_by_id(self, document_id: int, node_id: int) -> Optional[Dict[str, Any]]:
        """Belirli bir doküman düğümünün detayını getirir."""
        cursor = self.sqlite_conn.cursor()
        cursor.execute(
            f"SELECT {self._NODE_COLUMNS} FROM toc_nodes WHERE document_id = ? AND node_id = ?",
            (document_id, node_id)
        )
        row = cursor.fetchone()
        return dict(row) if row else None

    def get_children(self, document_id: int, parent_node_id: Optional[int]) -> List[Dict[str, Any]]:
        """
        Bir düğümün çocuklarını döndürür (recursive descent için). parent_node_id None ise
        kök düğümleri (en üst seviye) döndürür. Navigasyon için summary/is_leaf taşır.
        """
        cursor = self.sqlite_conn.cursor()
        cols = "node_id as id, heading, level, parent_id, path, is_leaf, summary, start_page, end_page"
        if parent_node_id is None:
            cursor.execute(
                f"SELECT {cols} FROM toc_nodes WHERE document_id = ? AND parent_id IS NULL ORDER BY node_id ASC",
                (document_id,)
            )
        else:
            cursor.execute(
                f"SELECT {cols} FROM toc_nodes WHERE document_id = ? AND parent_id = ? ORDER BY node_id ASC",
                (document_id, parent_node_id)
            )
        return [dict(row) for row in cursor.fetchall()]

    def query_chroma(self, document_id: int, query: str,
                     top_k: int = config.SEMANTIC_TOP_K,
                     node_ids: Optional[List[int]] = None) -> List[Dict[str, Any]]:
        """
        ChromaDB üzerinde anlamsal arama yapar.

        - Varsayılan: aramayı yalnızca ilgili document_id'ye göre filtreler (global / güvenlik ağı).
        - node_ids verilirse: aramayı yalnızca seçilen yaprak düğümlerine daraltır
          (recursive descent sonrası "yaprak-içi" arama — bkz. project_brain.md §6.7).
        """
        if node_ids:
            where = {"$and": [{"document_id": document_id}, {"node_id": {"$in": list(node_ids)}}]}
        else:
            where = {"document_id": document_id}

        query_embedding = self.embedder.embed_query(query)
        results = self.chroma_collection.query(
            query_embeddings=[query_embedding],
            n_results=top_k,
            where=where
        )

        parsed_results = []
        if results and results["documents"] and len(results["documents"][0]) > 0:
            for idx in range(len(results["documents"][0])):
                distance = results["distances"][0][idx] if results.get("distances") else 0.0
                # Cosine uzayı: distance = 1 - kosinüs_benzerliği. Benzerlik [0,1]'e kırpılır.
                similarity = max(0.0, round(1.0 - distance, 4))

                parsed_results.append({
                    "content": results["documents"][0][idx],
                    "metadata": results["metadatas"][0][idx],
                    "similarity": similarity
                })

        return parsed_results

    def delete_document(self, document_id: int):
        """SQLite ve ChromaDB'den dokümanı tamamen siler."""
        with self._write_lock:
            cursor = self.sqlite_conn.cursor()
            cursor.execute("DELETE FROM documents WHERE id = ?", (document_id,))
            cursor.execute("DELETE FROM toc_nodes WHERE document_id = ?", (document_id,))
            self.sqlite_conn.commit()

        try:
            self.chroma_collection.delete(where={"document_id": document_id})
        except Exception:
            pass

    def close(self):
        """SQLite bağlantısını kapatır."""
        self.sqlite_conn.close()
