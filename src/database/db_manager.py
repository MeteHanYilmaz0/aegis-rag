import sqlite3
import os
import json
import re
import requests
from typing import List, Dict, Any, Optional
import chromadb
from chromadb.api.types import Documents, Embeddings, EmbeddingFunction

# Projenin kök dizininde db klasörü oluşturma
DB_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "db"))
os.makedirs(DB_DIR, exist_ok=True)

SQLITE_PATH = os.path.join(DB_DIR, "aegis_rag.db")
CHROMA_PATH = os.path.join(DB_DIR, "chroma_db")

class OllamaEmbeddingFunction(EmbeddingFunction):
    """
    Ollama üzerinden yerel 'nomic-embed-text' modelini kullanan ChromaDB Uyumlu Embedding Sınıfı.
    Eğer Ollama kapalıysa veya model bulunamadıysa ChromaDB'nin varsayılan embedding yapısına yedeklenir.
    """
    def __init__(self, model_name: str = "nomic-embed-text", base_url: str = "http://localhost:11434"):
        self.model_name = model_name
        self.base_url = base_url
        self._fallback_fn = None

    def _get_fallback(self):
        if self._fallback_fn is None:
            from chromadb.utils.embedding_functions import DefaultEmbeddingFunction
            self._fallback_fn = DefaultEmbeddingFunction()
        return self._fallback_fn

    def __call__(self, input: Documents) -> Embeddings:
        embeddings = []
        try:
            for text in input:
                response = requests.post(
                    f"{self.base_url}/api/embeddings",
                    json={"model": self.model_name, "prompt": text},
                    timeout=5
                )
                if response.status_code == 200:
                    embeddings.append(response.json()["embedding"])
                else:
                    raise Exception(f"Ollama API Error: {response.status_code}")
            return embeddings
        except Exception:
            fallback = self._get_fallback()
            return fallback(input)


class DBManager:
    """
    SQLite ve ChromaDB veri tabanlarını koordine eden gelişmiş veri erişim katmanı.
    DAG bağlantıları, Çoklu Temsilcili Düğüm Çıpaları ve çapraz referansları yönetir.
    """
    def __init__(self):
        self.sqlite_conn = sqlite3.connect(SQLITE_PATH, check_same_thread=False)
        self.sqlite_conn.row_factory = sqlite3.Row
        self._init_sqlite()
        
        # ChromaDB yerel istemcisini başlat
        self.chroma_client = chromadb.PersistentClient(path=CHROMA_PATH)
        self.embedding_fn = OllamaEmbeddingFunction()
        self.chroma_collection = self.chroma_client.get_or_create_collection(
            name="aegis_rag_chunks",
            embedding_function=self.embedding_fn
        )

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
            batch_size = 10
            for i in range(0, total_chunks, batch_size):
                batch_ids = chroma_ids[i:i+batch_size]
                batch_docs = chroma_documents[i:i+batch_size]
                batch_metas = chroma_metadatas[i:i+batch_size]
                self.chroma_collection.add(
                    ids=batch_ids,
                    documents=batch_docs,
                    metadatas=batch_metas
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

    def query_chroma(self, document_id: int, query: str, top_k: int = 10) -> List[Dict[str, Any]]:
        """
        ChromaDB üzerinde anlamsal arama yapar.
        Aramayı sadece ilgili document_id'ye göre filtreler.
        """
        results = self.chroma_collection.query(
            query_texts=[query],
            n_results=top_k,
            where={"document_id": document_id}
        )
        
        parsed_results = []
        if results and results["documents"] and len(results["documents"][0]) > 0:
            for idx in range(len(results["documents"][0])):
                distance = results["distances"][0][idx] if "distances" in results and results["distances"] else 0.0
                similarity = round(1.0 - distance, 4) if distance <= 1.0 else 0.0
                
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
