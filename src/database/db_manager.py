import sqlite3
import os
import json
import re
import requests
from typing import List, Dict, Any, Optional
import chromadb

from src import config

# Projenin kök dizininde db klasörü oluşturma
DB_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "db"))
os.makedirs(DB_DIR, exist_ok=True)

SQLITE_PATH = os.path.join(DB_DIR, "aegis_rag.db")
CHROMA_PATH = os.path.join(DB_DIR, "chroma_db")

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
        """SQLite tablolarını ve graf ilişkilerini ilklendirir."""
        cursor = self.sqlite_conn.cursor()
        
        # 1. Doküman tablosu
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS documents (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                filename TEXT NOT NULL,
                file_path TEXT NOT NULL,
                upload_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # 2. Hiyerarşik TOC Düğümleri (Nodes) tablosu (Özet ve Centroid verileriyle)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS toc_nodes (
                id INTEGER PRIMARY KEY,
                document_id INTEGER NOT NULL,
                node_id INTEGER NOT NULL,
                heading TEXT NOT NULL,
                level INTEGER NOT NULL,
                parent_id INTEGER,
                path TEXT NOT NULL,
                content TEXT,
                summary TEXT,
                start_line INTEGER,
                end_line INTEGER,
                FOREIGN KEY (document_id) REFERENCES documents (id) ON DELETE CASCADE
            )
        """)
        
        # 3. Graf Referans Bağlantıları (DAG Links) tablosu
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS toc_links (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                document_id INTEGER NOT NULL,
                source_node_id INTEGER NOT NULL,
                target_node_id INTEGER NOT NULL,
                link_type TEXT NOT NULL, -- 'see_also', 'appendix', 'citation'
                FOREIGN KEY (document_id) REFERENCES documents (id) ON DELETE CASCADE
            )
        """)
        
        self.sqlite_conn.commit()

    def add_document(self, filename: str, file_path: str, toc_nodes: List[Dict[str, Any]], progress_callback=None) -> int:
        """
        Yeni bir dokümanı SQLite ve TOC düğümleriyle birlikte kaydeder.
        Ayrıca düğümleri anlamsal arama için ChromaDB'ye ekler ve graf kenarlarını (links) çıkarır.
        """
        cursor = self.sqlite_conn.cursor()
        
        # Dokümanı ekle
        cursor.execute(
            "INSERT INTO documents (filename, file_path) VALUES (?, ?)",
            (filename, file_path)
        )
        document_id = cursor.lastrowid
        
        # SQLite'a TOC düğümlerini ekle
        for node in toc_nodes:
            # Hafif özet çıkarma (Heterojen bulanıklığı önlemek için ilk 3 cümle + başlık)
            content = node["content"].strip()
            sentences = re.split(r'(?<=[.!?])\s+', content)
            summary_sentences = sentences[:3] if len(sentences) >= 3 else sentences
            summary = " ".join(summary_sentences) if summary_sentences else node["heading"]
            
            cursor.execute(
                """
                INSERT INTO toc_nodes 
                (document_id, node_id, heading, level, parent_id, path, content, summary, start_line, end_line)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    document_id,
                    node["id"],
                    node["heading"],
                    node["level"],
                    node["parent_id"],
                    node["path"],
                    content,
                    summary,
                    node["start_line"],
                    node["end_line"]
                )
            )

        # 4. Çapraz Referans Çıkarma (Best-Effort DAG Extraction)
        # Bölüm başlıklarını listele
        node_lookup = {n["heading"].lower(): n["id"] for n in toc_nodes}
        
        for node in toc_nodes:
            content = node["content"]
            # "Bkz. Bölüm X", "Bkz. X", "Bölüm Y'ye bakın" gibi kalıpları tara
            matches = re.findall(r'(?:bkz\.?|bakınız|bölüm|ek|tablo)\s+([A-ZÇĞİÖŞÜa-zçğıöşü0-9\s\.\-\:]+)', content, re.IGNORECASE)
            
            for match in matches:
                clean_match = match.strip().rstrip(".,;").lower()
                # Eğer eşleşen metin başka bir başlık düğümüyle eşleşiyorsa bağlantı oluştur
                for heading, target_id in node_lookup.items():
                    if clean_match in heading or heading in clean_match:
                        # Kendi kendine bağlantıyı önle
                        if node["id"] != target_id:
                            cursor.execute(
                                """
                                INSERT INTO toc_links (document_id, source_node_id, target_node_id, link_type)
                                VALUES (?, ?, ?, ?)
                                """,
                                (document_id, node["id"], target_id, "see_also")
                            )
                            break
                            
        self.sqlite_conn.commit()
        
        # ChromaDB'ye ekleme yap (Vektörlere parent_node_id ve hiyerarşik yol etiketleri eklenir)
        chroma_ids = []
        chroma_documents = []
        chroma_metadatas = []
        
        for node in toc_nodes:
            if node["content"].strip():
                # Düğümü paragraflara bölelim (daha yüksek arama hassasiyeti için)
                paragraphs = [p.strip() for p in node["content"].split("\n\n") if p.strip()]
                for p_idx, para in enumerate(paragraphs):
                    chroma_ids.append(f"doc_{document_id}_node_{node['id']}_p_{p_idx}")
                    chroma_documents.append(para)
                    chroma_metadatas.append({
                        "document_id": document_id,
                        "node_id": node["id"],
                        "path": node["path"],
                        "heading": node["heading"]
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

    def get_document_nodes(self, document_id: int) -> List[Dict[str, Any]]:
        """Bir dokümana ait tüm hiyerarşik TOC düğümlerini döndürür."""
        cursor = self.sqlite_conn.cursor()
        cursor.execute(
            """
            SELECT node_id as id, heading, level, parent_id, path, content, summary, start_line, end_line 
            FROM toc_nodes 
            WHERE document_id = ? 
            ORDER BY node_id ASC
            """,
            (document_id,)
        )
        rows = cursor.fetchall()
        return [dict(row) for row in rows]

    def get_node_by_id(self, document_id: int, node_id: int) -> Optional[Dict[str, Any]]:
        """Belirli bir doküman düğümünün detayını getirir."""
        cursor = self.sqlite_conn.cursor()
        cursor.execute(
            """
            SELECT node_id as id, heading, level, parent_id, path, content, summary, start_line, end_line 
            FROM toc_nodes 
            WHERE document_id = ? AND node_id = ?
            """,
            (document_id, node_id)
        )
        row = cursor.fetchone()
        return dict(row) if row else None

    def get_dag_links(self, document_id: int, node_id: int) -> List[Dict[str, Any]]:
        """Bir düğümden çıkan çapraz referanslı DAG komşu bağlantılarını getirir."""
        cursor = self.sqlite_conn.cursor()
        cursor.execute(
            """
            SELECT l.target_node_id, n.heading, n.path, l.link_type
            FROM toc_links l
            JOIN toc_nodes n ON l.document_id = n.document_id AND l.target_node_id = n.node_id
            WHERE l.document_id = ? AND l.source_node_id = ?
            """,
            (document_id, node_id)
        )
        rows = cursor.fetchall()
        return [dict(row) for row in rows]

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
        cursor = self.sqlite_conn.cursor()
        cursor.execute("DELETE FROM documents WHERE id = ?", (document_id,))
        cursor.execute("DELETE FROM toc_nodes WHERE document_id = ?", (document_id,))
        cursor.execute("DELETE FROM toc_links WHERE document_id = ?", (document_id,))
        self.sqlite_conn.commit()
        
        try:
            self.chroma_collection.delete(
                where={"document_id": document_id}
            )
        except Exception:
            pass

    def close(self):
        """SQLite bağlantısını kapatır."""
        self.sqlite_conn.close()
