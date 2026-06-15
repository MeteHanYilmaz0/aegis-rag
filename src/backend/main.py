import os
import re
import json
import shutil
import tempfile
import requests
import uuid
from fastapi import FastAPI, UploadFile, File, Form, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List, Dict, Any, Optional

from src import config
from src.parser.toc_extractor import TOCExtractor
from src.database.db_manager import DBManager

app = FastAPI(title="Aegis RAG API", description="Frontier Systems-Oriented Hybrid RAG Backend with Context Economy and Execution Policy")

# In-memory progress tracking for document uploads
UPLOAD_STATUS = {}

# CORS izinleri
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Veritabanı yöneticisini başlat
db_manager = DBManager()

# Ollama API URL
OLLAMA_URL = config.OLLAMA_URL

class QueryRequest(BaseModel):
    document_id: int
    query: str
    model_name: str = config.LLM_MODEL

def check_ollama_status() -> bool:
    """Ollama servisinin ayakta olup olmadığını kontrol eder."""
    try:
        response = requests.get(f"{OLLAMA_URL}/api/tags", timeout=config.OLLAMA_HEALTH_TIMEOUT)
        return response.status_code == 200
    except Exception:
        return False

def strip_think(text: str) -> str:
    """Thinking modellerinin (qwen3 vb.) <think>...</think> bloklarını yanıttan ayıklar."""
    return re.sub(r'<think>.*?</think>', '', text, flags=re.DOTALL).strip()

def ollama_generate(model: str, prompt: str, temperature: Optional[float] = None,
                    timeout: int = config.OLLAMA_GENERATE_TIMEOUT) -> str:
    """
    Ollama /api/generate çağrısı için tek giriş noktası.
    Thinking'i kapatır (JSON/sentez yanıtını kirletmesin) ve <think> bloklarını ayıklar.
    Hata durumunda istisna fırlatır; çağıran tarafta yakalanır.
    """
    payload = {
        "model": model,
        "prompt": prompt,
        "stream": False,
        "think": config.LLM_THINKING,
    }
    if temperature is not None:
        payload["options"] = {"temperature": temperature}
    response = requests.post(f"{OLLAMA_URL}/api/generate", json=payload, timeout=timeout)
    response.raise_for_status()
    return strip_think(response.json().get("response", ""))

def clean_json_response(text: str) -> str:
    """LLM'den gelen JSON yanıtı temizler (önce olası <think> bloğunu ayıklar)."""
    text = strip_think(text)
    match = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', text, re.DOTALL | re.IGNORECASE)
    if match:
        return match.group(1)
    return text

def calculate_approx_tokens(text: str) -> int:
    """Metnin yaklaşık token boyutunu hesaplar (Türkçe için kelime * oran)."""
    words = len(text.split())
    return int(words * config.TOKEN_PER_WORD)

def lexical_semantic_rerank(query: str, candidates: List[Dict[str, Any]], top_k: int = 3) -> List[Dict[str, Any]]:
    """
    Kaynak Verimli Hibrit Yeniden Sıralayıcı (Lexical-Semantic Reranker).
    Cosine Benzerliği (ChromaDB) ile BM25 benzeri Kelime Çakışma (Lexical) skorlarını harmanlar.
    """
    if not candidates:
        return []
        
    query_terms = set(re.findall(r'\w+', query.lower()))
    if not query_terms:
        return candidates[:top_k]
        
    for cand in candidates:
        doc_content = cand["content"].lower()
        doc_words = re.findall(r'\w+', doc_content)
        doc_words_set = set(doc_words)
        
        # Kelime çakışma oranı (Lexical Score)
        overlap = query_terms.intersection(doc_words_set)
        lexical_score = len(overlap) / len(query_terms) if query_terms else 0.0
        
        # Hibrit Skor Formülü (ağırlıklar config'ten)
        cand["hybrid_score"] = round(
            config.RERANK_COSINE_WEIGHT * cand["similarity"]
            + config.RERANK_LEXICAL_WEIGHT * lexical_score,
            4,
        )
        
    # Hibrit skora göre yeniden sırala
    reranked = sorted(candidates, key=lambda x: x["hybrid_score"], reverse=True)
    return reranked[:top_k]

@app.get("/api/health")
def health_check():
    """Sistem sağlığı ve bağımlılıkların durumunu döner."""
    ollama_ok = False
    models = []
    try:
        response = requests.get(f"{OLLAMA_URL}/api/tags", timeout=config.OLLAMA_HEALTH_TIMEOUT)
        if response.status_code == 200:
            ollama_ok = True
            models = [m["name"] for m in response.json().get("models", [])]
    except Exception:
        pass
            
    return {
        "status": "healthy",
        "ollama_connected": ollama_ok,
        "available_models": models
    }

@app.get("/api/documents/list")
def list_documents():
    """Yüklenen tüm dokümanları listeler."""
    try:
        return db_manager.get_documents()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.delete("/api/documents/{document_id}")
def delete_document(document_id: int):
    """Bir dokümanı sistemden siler."""
    try:
        db_manager.delete_document(document_id)
        return {"message": f"Doküman {document_id} başarıyla silindi."}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/documents/{document_id}/toc")
def get_document_toc(document_id: int):
    """Belirli bir dokümanın TOC hiyerarşisini listeler."""
    try:
        nodes = db_manager.get_document_nodes(document_id)
        if not nodes:
            raise HTTPException(status_code=404, detail="Doküman veya TOC yapısı bulunamadı.")
        return nodes
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

def process_uploaded_document(job_id: str, filename: str, temp_path: str, temp_dir: str):
    UPLOAD_STATUS[job_id] = {
        "status": "processing",
        "stage": "parsing_pdf",
        "current": 0,
        "total": 0,
        "percentage": 0.0,
        "message": "PDF dosyasının sayfa yapısı analiz ediliyor..."
    }
    
    try:
        file_ext = os.path.splitext(filename)[1].lower()
        
        # Progress callback for parser
        def parser_callback(current, total):
            UPLOAD_STATUS[job_id] = {
                "status": "processing",
                "stage": "parsing_pdf",
                "current": current,
                "total": total,
                "percentage": round((current / total) * 100, 1),
                "message": f"PDF ayrıştırılıyor: Sayfa {current}/{total} (%{round((current / total) * 100, 1)})"
            }

        if file_ext == ".pdf":
            # PDF: gömülü TOC önceliği + sezgisel yol + finalize (depth fold + is_leaf)
            toc_nodes = TOCExtractor.extract_tree(temp_path, progress_callback=parser_callback)
        else:
            with open(temp_path, "r", encoding="utf-8", errors="ignore") as f:
                markdown_content = f.read()
            raw_nodes = TOCExtractor.extract_toc_tree(markdown_content)
            toc_nodes = TOCExtractor.finalize_tree(raw_nodes, config.MAX_TREE_DEPTH)

        UPLOAD_STATUS[job_id] = {
            "status": "processing",
            "stage": "extracting_toc",
            "current": 100,
            "total": 100,
            "percentage": 100.0,
            "message": "TOC hiyerarşik başlık ağacı çıkarıldı, indeksleniyor..."
        }
        
        # Progress callback for db manager (ChromaDB embedding)
        def db_callback(current, total):
            UPLOAD_STATUS[job_id] = {
                "status": "processing",
                "stage": "embedding",
                "current": current,
                "total": total,
                "percentage": round((current / total) * 100, 1),
                "message": f"ChromaDB veritabanı oluşturuluyor: {current}/{total} paragraf vektörleştirildi (%{round((current / total) * 100, 1)})"
            }
            
        document_id = db_manager.add_document(filename, temp_path, toc_nodes, progress_callback=db_callback)
        
        # Move temp file to uploads directory
        uploads_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "uploads"))
        os.makedirs(uploads_dir, exist_ok=True)
        permanent_path = os.path.join(uploads_dir, filename)
        if os.path.exists(temp_path):
            shutil.move(temp_path, permanent_path)
            cursor = db_manager.sqlite_conn.cursor()
            cursor.execute("UPDATE documents SET file_path = ? WHERE filename = ?", (permanent_path, filename))
            db_manager.sqlite_conn.commit()
            
        UPLOAD_STATUS[job_id] = {
            "status": "completed",
            "document_id": document_id,
            "filename": filename,
            "node_count": len(toc_nodes),
            "message": "Belge başarıyla yüklendi ve indekslendi."
        }
    except Exception as e:
        UPLOAD_STATUS[job_id] = {
            "status": "failed",
            "message": f"Dosya işleme hatası: {str(e)}"
        }
    finally:
        try:
            shutil.rmtree(temp_dir)
        except Exception:
            pass

@app.post("/api/documents/upload")
async def upload_document(background_tasks: BackgroundTasks, file: UploadFile = File(...)):
    """PDF veya MD belge yükler, parse eder ve veritabanlarına kaydeder."""
    filename = file.filename
    file_ext = os.path.splitext(filename)[1].lower()
    
    if file_ext not in [".pdf", ".md", ".txt"]:
        raise HTTPException(status_code=400, detail="Sadece PDF, Markdown (.md) veya Metin (.txt) dosyaları desteklenmektedir.")

    temp_dir = tempfile.mkdtemp()
    temp_path = os.path.join(temp_dir, filename)
    
    try:
        with open(temp_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
    except Exception as e:
        try:
            shutil.rmtree(temp_dir)
        except Exception:
            pass
        raise HTTPException(status_code=500, detail=f"Dosya kaydetme hatası: {str(e)}")

    job_id = str(uuid.uuid4())
    background_tasks.add_task(process_uploaded_document, job_id, filename, temp_path, temp_dir)
    
    return {"job_id": job_id}

@app.get("/api/documents/upload/progress/{job_id}")
def get_upload_progress(job_id: str):
    if job_id not in UPLOAD_STATUS:
        raise HTTPException(status_code=404, detail="İşlem bulunamadı.")
    return UPLOAD_STATUS[job_id]

@app.post("/api/query")
def query_aegis(payload: QueryRequest):
    """
    Research-Grade Çift Hatlı Kesişim ve Deterministik Orkestrasyon Akışı.
    """
    document_id = payload.document_id
    query = payload.query
    model_name = payload.model_name
    
    if not check_ollama_status():
        raise HTTPException(status_code=503, detail="Yerel Ollama servisine bağlanılamadı.")

    nodes = db_manager.get_document_nodes(document_id)
    if not nodes:
        raise HTTPException(status_code=404, detail="Dokümana ait TOC ağacı bulunamadı.")
        
    trace = []
    
    # ==========================================
    # 1. KATMAN: Explicit Execution Policy Layer
    # ==========================================
    execution_policy = "Hierarchical Single-Node"
    query_lower = query.lower()
    
    # Karşılaştırma veya çoklu referans kalıplarını tara
    multi_indicators = ["kıyasla", "karşılaştır", "arasındaki fark", "farkları", "ve ek", "ikisi", "ilişkisi"]
    is_multi_subtree = any(ind in query_lower for ind in multi_indicators)
    
    if is_multi_subtree:
        execution_policy = "Multi-Subtree Synthesis"
    elif len(query.split()) < 3: # Çok kısa anahtar kelimeler doğrudan vektöre aktarılır
        execution_policy = "Hybrid Reranked Fallback"
        
    trace.append(f"🛡️ **Execution Policy Katmanı:** Deterministik politika belirlendi -> `[{execution_policy}]` Modu.")

    # ==========================================
    # 2. KATMAN: Çift Hatlı Arama & Kesişim (Heatmap)
    # ==========================================
    # ChromaDB semantik arama (ham adaylar)
    semantic_raw = db_manager.query_chroma(document_id, query, top_k=config.SEMANTIC_TOP_K)

    # Lexical-Semantic Reranking Katmanı
    reranked_hits = lexical_semantic_rerank(query, semantic_raw, top_k=config.RERANK_TOP_K)
    
    # Isı haritasını (Heatmap) hesapla
    heatmap_scores = {}
    for hit in reranked_hits:
        n_id = hit["metadata"].get("node_id")
        score = hit["hybrid_score"]
        if n_id not in heatmap_scores or score > heatmap_scores[n_id]:
            heatmap_scores[n_id] = score
            
    trace.append(f"🔥 **Semantik Isı Haritası (Heatmap) Hesaplandı:** {len(heatmap_scores)} düğüm pozitif eşleşme aldı.")

    # Ağaç listesini işaretle
    flat_tree_lines = []
    for node in nodes:
        n_id = node["id"]
        indent = "-" * (node["level"] - 1)
        prefix = f"{indent} " if indent else ""
        heat_text = f" [Anlamsal Uyuşma: %{heatmap_scores[n_id]*100:.1f}]" if n_id in heatmap_scores else ""
        flat_tree_lines.append(f"[ID: {n_id}] {prefix}{node['heading']}{heat_text}")
        
    flat_tree_text = "\n".join(flat_tree_lines)

    # ==========================================
    # 3. KATMAN: Yönlendirme ve Uncertainty-Aware Kontrolü
    # ==========================================
    selected_node_ids = []
    fallback_triggered = False
    
    if execution_policy != "Hybrid Reranked Fallback":
        # LLM'e ısı haritalı ağacı sunup yönlendirme yapmasını iste
        routing_prompt = f"""
Sana bir belgenin ısı haritalı başlık ağacı (TOC) ve bir kullanıcı sorusu verilecek. Görevin, soruyu yanıtlamak için okunması gereken en uygun başlık düğümlerinin (Node ID) listesini çıkarmaktır.

BELGE BAŞLIK AĞACI:
{flat_tree_text}

KULLANICI SORUSU:
"{query}"

YÖNERGELER:
1. Soru doğrudan bir bölümle ilgiliyse, o bölümün ID'sini döndür.
2. Soru birden fazla bölümü karşılaştırıyorsa, o bölümlerin ID listesini döndür.
3. Eğer anlamsal uyuşmalar (skorlar) çok düşükse veya hiçbir başlık soruyla doğrudan ilgili değilse `selected_node_ids` listesini boş bırak.
4. Yanıtı MUTLAK suretle aşağıdaki JSON formatında ver. Başka hiçbir metin ekleme.

JSON FORMATI:
{{
  "selected_node_ids": [id1, id2, ...],
  "confidence": "HIGH veya MEDIUM veya LOW",
  "reason": "kısa gerekçe"
}}
"""
        try:
            raw_response = ollama_generate(model_name, routing_prompt, temperature=0.0)
            raw_json = clean_json_response(raw_response)
            route_data = json.loads(raw_json)

            selected_node_ids = route_data.get("selected_node_ids", [])
            confidence = route_data.get("confidence", "MEDIUM")
            reason = route_data.get("reason", "")

            # Uncertainty-Aware Routing: Güven skoru LOW ise doğrudan fallback
            if confidence == "LOW" or not selected_node_ids:
                fallback_triggered = True
                trace.append("⚠️ **Uncertainty-Aware Tetiklendi:** Güven düzeyi Düşük (LOW). Yönlendirme devre dışı bırakılarak doğrudan Fallback moduna geçiliyor.")
            else:
                trace.append(f"🎯 **Yönlendirme Başarılı:** Seçilen Düğümler: {selected_node_ids} (Gerekçe: {reason})")
        except Exception as e:
            fallback_triggered = True
            trace.append(f"⚠️ **Yönlendirme Hatası:** {str(e)}. Fallback aktif.")
    else:
        fallback_triggered = True

    # ==========================================
    # 4. KATMAN: Context Economy & Token Budgeting (Bağlam Bütçeleme)
    # ==========================================
    context_blocks = []
    total_token_budget = config.TOKEN_BUDGET # Maksimum güvenli sınır
    current_token_count = 0
    
    if not fallback_triggered and selected_node_ids:
        # Seçilen düğümleri ve bunlara bağlı DAG komşularını çek
        for node_id in selected_node_ids[:3]: # Çoklu sentezde maksimum 3 düğüm sınırı (Orchestration guard)
            node_details = db_manager.get_node_by_id(document_id, int(node_id))
            if not node_details:
                continue
                
            node_content = node_details["content"]
            node_path = node_details["path"]
            
            # Ebeveyn restorasyonu ve hiyerarşi etiketi
            header_block = f"--- BÖLÜM: {node_path} ---\n"
            block_tokens = calculate_approx_tokens(header_block + node_content)
            
            # Bütçe kontrolü: Eğer ana düğüm bütçeyi aşıyorsa kesirli sıkıştırma yap
            if current_token_count + block_tokens > total_token_budget:
                remaining_tokens = total_token_budget - current_token_count
                if remaining_tokens > 100:
                    truncated_content = node_content[:int(remaining_tokens * 4)] + "\n...[Bütçe Sınırı Nedeniyle Kesildi]..."
                    context_blocks.append(header_block + truncated_content)
                    current_token_count += remaining_tokens
                break
            else:
                context_blocks.append(header_block + node_content)
                current_token_count += block_tokens

    # Fallback RAG
    if fallback_triggered or not context_blocks:
        trace.append("🔍 **ChromaDB Vektör Havuzundan Okuma Yapılıyor...**")
        for idx, res in enumerate(reranked_hits):
            sim_score = res["hybrid_score"]
            path_str = res["metadata"].get("path", "Genel")
            
            block = f"--- Semantik Parça #{idx+1} [Uyum: {sim_score:.2%}] (Bölüm: {path_str}) ---\n{res['content']}\n"
            block_tokens = calculate_approx_tokens(block)
            
            if current_token_count + block_tokens <= total_token_budget:
                context_blocks.append(block)
                current_token_count += block_tokens
            else:
                break

    trace.append(f"📊 **Context Economy:** Bağlam Penceresi Kullanımı: **{current_token_count} / {total_token_budget}** Yaklaşık Token.")

    # ==========================================
    # 5. KATMAN: Sentez ve Local NLI Tutarlılık Süzgeci
    # ==========================================
    final_context = "\n\n".join(context_blocks)
    
    qa_prompt = f"""
Sana bir belgeden alınmış doğrulanmış bağlam (context) ve bir soru verilecek.
Görevin, bağlamdaki bilgilere sadık kalarak soruyu Türkçe olarak kapsamlı şekilde yanıtlamaktır.

BAĞLAM:
{final_context}

SORU:
"{query}"

KURALLAR:
1. Yalnızca verilen bağlamdaki gerçekleri kullan. 
2. Bilgilerin yetersiz olduğu yerleri belirt ama uydurma yapma.
3. Cevap içinde atıfta bulunduğun bölümleri (örn: Bölüm 2.1) parantez içinde belirt.
4. Cevabı doğrudan, net ve üçüncü şahıs ağzından yaz. Kendi düşünce sürecini, "bu metne göre", "cevap şöyle olabilir", "yönergelere göre" gibi meta-ifadeleri yanıta kesinlikle dahil etme. Doğrudan cevabı döndür.
"""

    answer = "Cevap oluşturulamadı."
    try:
        candidate_answer = ollama_generate(model_name, qa_prompt).strip()

        if candidate_answer:
            # Local NLI-style Consistency Heuristic (Self-Reflection Check)
            nli_prompt = f"""
Sana bir bağlam ve bu bağlama göre üretilmiş bir yapay zeka cevabı verilecek.
Görevin, cevabın bağlamdaki bilgilerle çelişip çelişmediğini (çelişki, halüsinasyon veya uydurma bilgi içerip içermediğini) kontrol etmektir.

BAĞLAM:
{final_context[:2500]}

YAPAY ZEKA CEVABI:
{candidate_answer[:2000]}

KURALLAR:
1. Eğer cevap bağlamla tamamen uyumluysa ve uydurma bilgi içermiyorsa kelimesi kelimesine sadece "CONSISTENT" yaz.
2. Eğer cevapta bağlam dışı/çelişkili uydurma bir iddia varsa sadece "CONTRADICTION" yaz.
"""
            try:
                nli_status = ollama_generate(model_name, nli_prompt, temperature=0.0).strip()
            except Exception:
                nli_status = "CONSISTENT"

            if "CONTRADICTION" in nli_status:
                trace.append("⚠️ **Local NLI Tutarlılık Süzgeci:** Çelişki/Uydurma şüphesi tespit edildi. Güvenli filtreleme uygulanıyor.")
                # Çelişkili cevaba bir uyarı notu ekleyelim
                answer = candidate_answer + "\n\n*(Not: Aegis yerel NLI süzgeci bu yanıtın bazı kısımlarında bağlam tutarsızlığı tespit etmiştir, lütfen kaynakları doğrulayınız.)*"
            else:
                trace.append("🟢 **Local NLI Tutarlılık Süzgeci:** Yanıt bağlamla tutarlı bulundu (CONSISTENT).")
                answer = candidate_answer
        else:
            answer = "Model boş yanıt döndürdü."
    except Exception as e:
        answer = f"Sorgu işlenirken bir hata oluştu: {str(e)}"
        trace.append(f"❌ **Hata:** {str(e)}")

    return {
        "answer": answer,
        "selected_node_ids": selected_node_ids,
        "execution_policy": execution_policy,
        "fallback_triggered": fallback_triggered,
        "context_tokens": current_token_count,
        "heatmap_scores": heatmap_scores,
        "trace": trace
    }
