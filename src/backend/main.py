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
        "keep_alive": config.OLLAMA_KEEP_ALIVE,
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

# ==========================================================================
# Faz 3: Recursive Descent Navigasyonu + Yaprak-içi Scoped Retrieval
# ==========================================================================

def _descent_prompt(query: str, frontier: List[Dict[str, Any]], heatmap: Dict[Any, float]) -> str:
    lines = []
    for n in frontier:
        nid = n["id"]
        score = heatmap.get(nid, heatmap.get(str(nid)))
        heat = f" [uyuşma %{score * 100:.0f}]" if score else ""
        kind = "alt-başlıkları var" if not n.get("is_leaf") else "yaprak"
        summary = (n.get("summary") or "").strip().replace("\n", " ")[:200]
        lines.append(f"[ID:{nid}] {n['heading']} ({kind}){heat}\n    özet: {summary}")
    tree_text = "\n".join(lines)
    return f"""Sana bir belgenin bir seviyesindeki başlıklar (özetleriyle) ve bir kullanıcı sorusu verilecek.
Görevin: soruyu yanıtlamak için hangi başlıklara İNMEK (descend; alt-başlıklarına bakmak) ve
hangilerini doğrudan OKUMAK (select) gerektiğine karar vermektir.

BAŞLIKLAR:
{tree_text}

KULLANICI SORUSU:
"{query}"

YÖNERGELER:
1. Cevabın doğrudan içinde olabileceği başlıkları "select"e ekle.
2. İlgili ama daha derine inilmesi gereken (alt-başlığı olan) başlıkları "descend"e ekle.
3. Karşılaştırma/listeleme sorularında ilgili TÜM başlıkları seç.
4. Hiçbiri ilgili değilse iki listeyi de boş bırak ve confidence "LOW" ver.
5. SADECE şu JSON'u döndür, başka hiçbir metin ekleme:
{{"select": [id, ...], "descend": [id, ...], "confidence": "HIGH|MEDIUM|LOW"}}"""


def _ask_descent(model_name: str, query: str, frontier: List[Dict[str, Any]], heatmap: Dict[Any, float]):
    try:
        raw = ollama_generate(model_name, _descent_prompt(query, frontier, heatmap), temperature=0.0)
        data = json.loads(clean_json_response(raw))
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def _cap_frontier(frontier: List[Dict[str, Any]], heatmap: Dict[Any, float], cap: int = 25):
    """Çok geniş seviyelerde (örn. yüzlerce L1) frontier'ı ısı haritasına göre buda."""
    if len(frontier) <= cap:
        return frontier
    def sc(n):
        return heatmap.get(n["id"], heatmap.get(str(n["id"]), 0)) or 0
    return sorted(frontier, key=sc, reverse=True)[:cap]


def _valid_ids(raw_list, present_ids):
    out = []
    for i in raw_list or []:
        if isinstance(i, (int, str)) and str(i).isdigit() and int(i) in present_ids:
            out.append(int(i))
    return out


def _aggregate_heatmap(heatmap, children_map):
    """
    Her düğüme alt-ağacındaki EN YÜKSEK ısı skorunu yayar (ancestor aggregation).
    İç düğüm başlıklarının içeriği/özeti çoğu zaman bilgisizdir; bu yayılım sayesinde
    descent kök seviyede "ilgili içerik bu dalın altında" sinyalini görebilir
    (örn. "LVEF" kök başlıklarda geçmese de Proje 1 alt-ağacında eşleştiği için parlar).
    """
    agg = {}

    def visit(nid):
        if nid in agg:
            return agg[nid]
        best = heatmap.get(nid, heatmap.get(str(nid), 0)) or 0
        agg[nid] = best  # döngü koruması (ağaçta yok ama güvenli)
        for ch in children_map.get(nid, []):
            best = max(best, visit(ch["id"]))
        agg[nid] = best
        return best

    for root in children_map.get(None, []):
        visit(root["id"])
    return agg


def run_recursive_descent(document_id, query, children_map, node_map, heatmap, model_name, trace):
    """
    Ağaçta seviye seviye inerek soruyla ilgili hedef düğümleri toplar.
    Döner: (targets, confidence, descended). descended = inilen iç (bölüm başlığı)
    düğümleri; listeleme/genel-bakış sorularında bunların özetleri de kullanılır.
    """
    targets = set()
    descended = set()
    visited = set()
    frontier = list(children_map.get(None, []))
    calls = 0
    confidence = "MEDIUM"

    while frontier and calls < config.MAX_DESCENT_CALLS:
        present = _cap_frontier(frontier, heatmap)
        data = _ask_descent(model_name, query, present, heatmap)
        calls += 1
        if data is None:
            confidence = "LOW"
            break

        confidence = data.get("confidence", "MEDIUM")
        present_ids = {n["id"] for n in present}
        select_ids = _valid_ids(data.get("select"), present_ids)
        descend_ids = _valid_ids(data.get("descend"), present_ids)

        targets.update(select_ids)

        next_frontier = []
        for nid in descend_ids:
            kids = children_map.get(nid, [])
            if kids:
                next_frontier.extend(kids)
                descended.add(nid)  # inilen bölüm başlığı (özeti listelemede kullanılır)
            else:
                targets.add(nid)  # yaprak: daha derine inilemez, hedef say

        if select_ids or descend_ids:
            trace.append(f"🧭 **Descent (tur {calls}):** okunacak={select_ids or '-'} · inilen={descend_ids or '-'}")

        if not descend_ids:
            break
        visited.update(present_ids)
        frontier = [n for n in next_frontier if n["id"] not in visited]

    return targets, confidence, descended


_ABSTRACT_HEADINGS = {"özet", "abstract", "ozet", "öz", "summary"}


def _abstract_node_ids(nodes):
    """
    ÖZET/ABSTRACT düğümlerinin id'leri. Bunlar belgenin en bilgi-yoğun bölümleridir;
    factual sorularda kesin bilgi (veri kaynakları, sınırlılıklar vb.) genelde buradadır.
    Her sorgunun kapsamına eklenir — rerank alakasızsa zaten elemekte, riski düşük.
    """
    return {n["id"] for n in nodes if (n.get("heading") or "").strip().lower() in _ABSTRACT_HEADINGS}


def _subtree_ids(root_ids, children_map):
    """Verilen düğümler + tüm alt-ağaç (descendant) node_id kümesi (scoped arama kapsamı)."""
    out = set()
    stack = list(root_ids)
    while stack:
        nid = stack.pop()
        if nid in out:
            continue
        out.add(nid)
        for ch in children_map.get(nid, []):
            stack.append(ch["id"])
    return out


@app.post("/api/query")
def query_aegis(payload: QueryRequest):
    """Hiyerarşik recursive descent + yaprak-içi scoped retrieval + alıntılı sentez."""
    document_id = payload.document_id
    query = payload.query
    model_name = payload.model_name

    if not check_ollama_status():
        raise HTTPException(status_code=503, detail="Yerel Ollama servisine bağlanılamadı.")

    nodes = db_manager.get_document_nodes(document_id)
    if not nodes:
        raise HTTPException(status_code=404, detail="Dokümana ait TOC ağacı bulunamadı.")

    trace = []
    node_map = {n["id"]: n for n in nodes}
    children_map: Dict[Any, List[Dict[str, Any]]] = {}
    for n in nodes:
        children_map.setdefault(n["parent_id"], []).append(n)

    # ==========================================
    # 1. Heatmap (global çift-hat: ChromaDB + rerank) — UI + descent ipucu
    # ==========================================
    # Sorgu embedding'i bir kez hesaplanır; hem global heatmap hem scoped aramada paylaşılır.
    query_embedding = db_manager.embedder.embed_query(query)
    semantic_raw = db_manager.query_chroma(document_id, query, top_k=config.SEMANTIC_TOP_K,
                                           query_embedding=query_embedding)
    reranked_hits = lexical_semantic_rerank(query, semantic_raw, top_k=config.RERANK_TOP_K)
    heatmap_scores = {}
    for hit in reranked_hits:
        n_id = hit["metadata"].get("node_id")
        score = hit["hybrid_score"]
        if n_id not in heatmap_scores or score > heatmap_scores[n_id]:
            heatmap_scores[n_id] = score
    trace.append(f"🔥 **Semantik Isı Haritası:** {len(heatmap_scores)} düğüm pozitif eşleşme aldı.")

    # ==========================================
    # 2. Recursive Descent (seviye seviye iniş)
    # ==========================================
    # Descent için ısıyı atalara yay (iç düğüm başlıkları opak olduğundan kritik).
    descent_heatmap = _aggregate_heatmap(heatmap_scores, children_map)
    targets, confidence, descended = run_recursive_descent(
        document_id, query, children_map, node_map, descent_heatmap, model_name, trace
    )
    descent_ok = bool(targets) and confidence != "LOW"
    if descent_ok:
        trace.append(f"🎯 **Descent tamamlandı:** hedef düğümler {sorted(targets)} (güven: {confidence}).")
    else:
        trace.append("⚠️ **Descent zayıf/sonuçsuz → ısı haritası kapsamına geçiliyor.**")

    # ==========================================
    # 3. Bağlam: özetler + kapsam-içi pasajlar (Context Economy)
    # ==========================================
    context_blocks = []
    current_token_count = 0
    budget = config.TOKEN_BUDGET

    # ÇİFT-HAT KESİŞİMİ: kapsam = yapısal hedefler (descent) ∪ ısı-haritası zirvesi.
    # Descent yanlış dala inse bile (örn. tez Q3: descent ch5'e indi ama içerik ch3'te,
    # ısı ch3'ü işaret etti) vektör sinyali telafi eder.
    heatmap_top = [nid for nid, _ in sorted(heatmap_scores.items(), key=lambda kv: kv[1], reverse=True)[:3]]
    abstract_ids = _abstract_node_ids(nodes)  # ÖZET/ABSTRACT'ı her zaman kapsama kat
    structural_roots = set(targets) | set(descended)
    scope_roots = structural_roots | set(heatmap_top) | abstract_ids

    if scope_roots:
        # 3a. Özetler: önce yapısal hedefler (en sığ = bölüm başlıkları), sonra ısı-zirvesi.
        summary_budget = int(budget * 0.5)
        summary_ids = sorted(structural_roots, key=lambda i: (node_map[i]["level"] if i in node_map else 99, i))
        summary_ids += [n for n in heatmap_top if n not in structural_roots]
        for tid in summary_ids:
            node = node_map.get(tid)
            summ = (node.get("summary") or "").strip() if node else ""
            if summ:
                block = f"[ÖZET — {node['path']}]: {summ}"
                t = calculate_approx_tokens(block)
                if current_token_count + t <= summary_budget:
                    context_blocks.append(block)
                    current_token_count += t

        # 3b. Kapsam-içi scoped pasaj araması (yapısal + ısı-zirvesi alt-ağaçları İÇİNDE)
        scope = list(_subtree_ids(scope_roots, children_map))
        scoped = db_manager.query_chroma(document_id, query, top_k=config.SCOPED_TOP_K,
                                         node_ids=scope, query_embedding=query_embedding)
        scoped = lexical_semantic_rerank(query, scoped, top_k=config.CONTEXT_PASSAGES)
        for res in scoped:
            meta = res["metadata"]
            page = meta.get("start_page")
            page_str = f", s.{page}" if page and page != -1 else ""
            block = f"--- Pasaj ({meta.get('path', 'Genel')}{page_str}) ---\n{res['content']}"
            t = calculate_approx_tokens(block)
            if current_token_count + t <= budget:
                context_blocks.append(block)
                current_token_count += t
            else:
                break
        trace.append(f"🔎 **Kapsam-içi arama:** {len(scope)} düğüm (yapısal {sorted(structural_roots) or '-'} + ısı {heatmap_top} + özet {sorted(abstract_ids) or '-'}) → {len(scoped)} pasaj bağlama alındı.")

    # 3c. Tam global fallback: ne yapısal hedef ne ısı sinyali varsa
    if not context_blocks:
        trace.append("🔍 **Global ChromaDB okuması (kapsam yok)...**")
        for idx, res in enumerate(reranked_hits):
            meta = res["metadata"]
            page = meta.get("start_page")
            page_str = f", s.{page}" if page and page != -1 else ""
            block = f"--- Semantik Parça #{idx + 1} ({meta.get('path', 'Genel')}{page_str}) ---\n{res['content']}"
            t = calculate_approx_tokens(block)
            if current_token_count + t <= budget:
                context_blocks.append(block)
                current_token_count += t
            else:
                break

    fallback_triggered = not descent_ok
    trace.append(f"📊 **Context Economy:** {current_token_count} / {budget} yaklaşık token.")

    # ==========================================
    # 4. Sentez (alıntılı, grounding; Self-NLI kaldırıldı)
    # ==========================================
    final_context = "\n\n".join(context_blocks)
    qa_prompt = f"""Sana bir belgeden seçilmiş bağlam (özetler + pasajlar) ve bir soru verilecek.
Görevin, YALNIZCA bağlamdaki bilgilere dayanarak soruyu Türkçe ve doğrudan yanıtlamak.

BAĞLAM:
{final_context}

SORU:
"{query}"

KURALLAR:
1. Sadece bağlamdaki gerçekleri kullan; bağlamda olmayan bir şeyi UYDURMA.
2. Bilgi bağlamda yoksa açıkça "Belgede bu bilgi bulunmuyor." de.
3. Atıf yaptığın yeri parantez içinde belirt (bölüm yolu ve varsa sayfa).
4. Doğrudan cevabı yaz; "bağlama göre", "yönergelere göre" gibi meta-ifadeleri kullanma.
"""
    answer = "Cevap oluşturulamadı."
    try:
        candidate = ollama_generate(model_name, qa_prompt).strip()
        answer = candidate if candidate else "Model boş yanıt döndürdü."
    except Exception as e:
        answer = f"Sorgu işlenirken bir hata oluştu: {str(e)}"
        trace.append(f"❌ **Hata:** {str(e)}")

    # UI vurgusu: yapısal hedefler varsa onları, yoksa ısı-zirvesini göster.
    selected = sorted(structural_roots) if structural_roots else sorted(heatmap_top)

    return {
        "answer": answer,
        "selected_node_ids": selected,
        "execution_policy": "Recursive Descent" if descent_ok else "Isı-Kapsamlı Fallback",
        "fallback_triggered": fallback_triggered,
        "context_tokens": current_token_count,
        "heatmap_scores": heatmap_scores,
        "trace": trace,
    }
