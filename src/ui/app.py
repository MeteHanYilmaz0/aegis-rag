import streamlit as st
import requests
import os
import json
import time
import codecs

# Sayfa Yapılandırması
st.set_page_config(
    page_title="Aegis RAG - Frontier Belge Zekası",
    page_icon="🛡️",
    layout="wide",
    initial_sidebar_state="expanded"
)

# API ve Sunucu Tanımlamaları
BACKEND_URL = "http://127.0.0.1:8002"

# Premium Koyu Tema Özel CSS Kodları
custom_css = """
<style>
    @import url('https://fonts.googleapis.com/css2?family=Outfit:wght@300;400;500;600;700&display=swap');
    
    html, body, [class*="css"] {
        font-family: 'Outfit', sans-serif;
    }
    
    .main {
        background-color: #080b11;
        color: #e2e8f0;
    }
    
    /* Degrade Başlık Stili */
    .gradient-text {
        background: linear-gradient(135deg, #00f2fe 0%, #4facfe 100%);
        -webkit-background-clip: text;
        -webkit-text-fill-color: transparent;
        font-weight: 700;
        font-size: 2.8rem;
        margin-bottom: 0.2rem;
    }
    
    .gradient-subtitle {
        color: #94a3b8;
        font-size: 1.1rem;
        margin-bottom: 2rem;
        font-weight: 300;
    }
    
    /* Kart Yapıları (Glassmorphism) */
    .glass-card {
        background: rgba(18, 24, 38, 0.6);
        border: 1px solid rgba(255, 255, 255, 0.03);
        border-radius: 16px;
        padding: 20px;
        backdrop-filter: blur(10px);
        margin-bottom: 20px;
        box-shadow: 0 8px 32px 0 rgba(0, 0, 0, 0.3);
        transition: transform 0.2s ease, border-color 0.2s ease;
    }
    
    .glass-card:hover {
        transform: translateY(-2px);
        border-color: rgba(0, 242, 254, 0.2);
    }
    
    /* Sol Menü Arka Planı */
    [data-testid="stSidebar"] {
        background-color: #05070c;
        border-right: 1px solid rgba(255, 255, 255, 0.03);
    }
    
    /* Durum Göstergeleri (Rozet) */
    .status-badge {
        padding: 4px 10px;
        border-radius: 9999px;
        font-size: 0.8rem;
        font-weight: 600;
        display: inline-block;
    }
    .status-online {
        background-color: rgba(16, 185, 129, 0.1);
        color: #10b981;
        border: 1px solid rgba(16, 185, 129, 0.2);
    }
    .status-offline {
        background-color: rgba(239, 68, 68, 0.1);
        color: #ef4444;
        border: 1px solid rgba(239, 68, 68, 0.2);
    }
    
    /* Sohbet Mesaj Balonları */
    .chat-bubble-user {
        background-color: #151d2a;
        border-left: 4px solid #4facfe;
        border-radius: 12px;
        padding: 14px 18px;
        margin: 10px 0;
        box-shadow: 0 4px 12px rgba(0, 0, 0, 0.15);
    }
    
    .chat-bubble-bot {
        background-color: #0c101a;
        border-left: 4px solid #00f2fe;
        border-radius: 12px;
        padding: 14px 18px;
        margin: 10px 0;
        box-shadow: 0 4px 16px rgba(0, 0, 0, 0.25);
        border-right: 1px solid rgba(255, 255, 255, 0.01);
    }
    
    /* Tree Trace Akış Adımları */
    .trace-container {
        margin-top: 15px;
        padding: 15px;
        border-radius: 12px;
        background-color: #04060b;
        border: 1px dashed rgba(255, 255, 255, 0.05);
    }
    
    .trace-title {
        font-size: 0.95rem;
        font-weight: 600;
        color: #38bdf8;
        margin-bottom: 10px;
        display: flex;
        align-items: center;
        gap: 6px;
    }
    
    .trace-step {
        font-size: 0.85rem;
        color: #cbd5e1;
        padding: 6px 0;
        border-left: 2px solid rgba(56, 189, 248, 0.15);
        padding-left: 12px;
        margin-left: 6px;
        position: relative;
    }
    .trace-step::before {
        content: '';
        position: absolute;
        left: -5px;
        top: 10px;
        width: 8px;
        height: 8px;
        border-radius: 50%;
        background-color: #38bdf8;
    }
    
    /* Token ve Yönlendirme Dashboard Rozetleri */
    .dashboard-container {
        display: flex;
        gap: 10px;
        margin-top: 5px;
        margin-bottom: 12px;
    }
    .dashboard-badge {
        padding: 6px 12px;
        border-radius: 8px;
        font-size: 0.8rem;
        font-weight: 500;
        background: rgba(255, 255, 255, 0.02);
        border: 1px solid rgba(255, 255, 255, 0.05);
    }
    .badge-policy { color: #f59e0b; border-color: rgba(245, 158, 11, 0.2); }
    .badge-token { color: #38bdf8; border-color: rgba(56, 189, 248, 0.2); }
    .badge-fallback { color: #ef4444; border-color: rgba(239, 68, 68, 0.2); }
    
    /* Isı Haritalı Ağaç Düğümleri (Dynamic Sidebar Heatmap) */
    .tree-node-base {
        padding: 4px 6px;
        border-radius: 4px;
        margin: 2px 0;
        transition: background-color 0.3s ease, border 0.3s ease;
        display: flex;
        justify-content: space-between;
        align-items: center;
    }
    
    /* Isı Seviyeleri (Renk Degradeleri) */
    .heat-high { background-color: rgba(16, 185, 129, 0.15); border-left: 3px solid #10b981; color: #10b981 !important; font-weight: 600; }
    .heat-medium { background-color: rgba(56, 189, 248, 0.12); border-left: 3px solid #38bdf8; color: #38bdf8 !important; }
    .heat-low { background-color: rgba(245, 158, 11, 0.08); border-left: 3px solid #f59e0b; color: #f59e0b !important; }
    
    /* Aktif Yönlendirilen Düğümler (LLM Seçimleri) */
    .node-selected {
        border: 1px solid #00f2fe !important;
        background-color: rgba(0, 242, 254, 0.08) !important;
        box-shadow: 0 0 10px rgba(0, 242, 254, 0.15);
    }
    
    .tree-node-1 { padding-left: 0px; font-weight: 600; font-size: 0.95rem; }
    .tree-node-2 { padding-left: 15px; font-weight: 500; font-size: 0.9rem; }
    .tree-node-3 { padding-left: 30px; font-style: italic; font-size: 0.85rem; }
    .tree-node-4 { padding-left: 45px; font-style: italic; font-size: 0.8rem; }
    .tree-node-5 { padding-left: 60px; font-style: italic; font-size: 0.75rem; }
    
    .heat-score {
        font-size: 0.7rem;
        background: rgba(255, 255, 255, 0.05);
        padding: 2px 5px;
        border-radius: 4px;
        font-weight: 600;
    }
</style>
"""
st.markdown(custom_css, unsafe_allow_html=True)

# 1. Sunucu Sağlık Kontrolü
backend_online = False
ollama_online = False
models = []
health_error_message = None

try:
    health_response = requests.get(f"{BACKEND_URL}/api/health", timeout=10)
    if health_response.status_code == 200:
        backend_online = True
        health_data = health_response.json()
        ollama_online = health_data.get("ollama_connected", False)
        models = health_data.get("available_models", [])
except Exception as e:
    backend_online = False
    health_error_message = str(e)

# Sidebar Tasarımı
with st.sidebar:
    st.markdown('<div style="text-align: center; padding: 10px 0;"><h1 style="color: #00f2fe; margin-bottom: 0; font-size: 2.2rem; font-weight: 800; letter-spacing: 1px;">🛡️ AEGIS RAG</h1><p style="color: #64748b; font-size: 0.85rem; margin-top: 5px;">Frontier Document Intelligence</p></div>', unsafe_allow_html=True)
    st.markdown("---")
    
    st.markdown("##### Sistem Durumu")
    if health_error_message:
        st.sidebar.error(f"Hata: {health_error_message}")
    status_cols = st.columns(2)
    with status_cols[0]:
        if backend_online:
            st.markdown('<div class="status-badge status-online">🟢 Backend OK</div>', unsafe_allow_html=True)
        else:
            st.markdown('<div class="status-badge status-offline">🔴 Backend Hata</div>', unsafe_allow_html=True)
            
    with status_cols[1]:
        if ollama_online:
            st.markdown('<div class="status-badge status-online">🟢 Ollama OK</div>', unsafe_allow_html=True)
        else:
            st.markdown('<div class="status-badge status-offline">🔴 Ollama Hata</div>', unsafe_allow_html=True)
            
    st.markdown("---")
    
    st.markdown("##### Yapılandırma")
    default_models = ["qwen3:8b", "qwen2.5:7b-instruct", "llama3:latest"]
    available_models_list = models if models else default_models
    preferred_model = "qwen3:8b"

    selected_model = st.selectbox(
        "Dil Modeli (LLM)",
        options=available_models_list,
        index=available_models_list.index(preferred_model) if preferred_model in available_models_list else 0,
        help="Ollama üzerinde çalışan yerel akıl yürütme modeli."
    )
    
    st.markdown("##### Belge Yükle")
    uploaded_file = st.file_uploader(
        "PDF veya MD Belgesi",
        type=["pdf", "md", "txt"],
        help="Sistem tarafından otomatik olarak hiyerarşik ağaç yapısına parse edilecektir."
    )
    
    if uploaded_file is not None:
        if st.button("Belgeyi İndeksle ⚡", use_container_width=True):
            progress_bar = st.progress(0.0)
            status_text = st.empty()
            
            files = {"file": (uploaded_file.name, uploaded_file.getvalue(), uploaded_file.type)}
            try:
                # Arka plan görevini tetikle
                upload_response = requests.post(f"{BACKEND_URL}/api/documents/upload", files=files)
                if upload_response.status_code == 200:
                    job_data = upload_response.json()
                    job_id = job_data.get("job_id")
                    
                    # Polling döngüsü
                    completed = False
                    while not completed:
                        time.sleep(0.5)
                        progress_res = requests.get(f"{BACKEND_URL}/api/documents/upload/progress/{job_id}")
                        if progress_res.status_code == 200:
                            progress_data = progress_res.json()
                            status = progress_data.get("status")
                            percentage = progress_data.get("percentage", 0.0)
                            message = progress_data.get("message", "Bekleniyor...")
                            
                            # İlerleme çubuğunu ve durum mesajını güncelle
                            progress_bar.progress(float(percentage / 100.0))
                            status_text.markdown(f"⚡ **{message}**")
                            
                            if status == "completed":
                                completed = True
                                st.success(f"🟢 Başarıyla İndekslendi: {progress_data.get('node_count')} alt bölüm tespit edildi.")
                                time.sleep(1.5)
                                st.rerun()
                            elif status == "failed":
                                completed = True
                                st.error(f"❌ İndeksleme Başarısız: {progress_data.get('message')}")
                        else:
                            completed = True
                            st.error("❌ İlerleme durumu okunamadı.")
                else:
                    st.error(f"❌ Hata: {upload_response.json().get('detail', 'Yükleme başlatılamadı.')}")
            except Exception as e:
                st.error(f"❌ Bağlantı hatası: {str(e)}")
                    
    st.markdown("---")
    
    st.markdown("##### Aktif Dokümanlar")
    documents = []
    if backend_online:
        try:
            doc_list_res = requests.get(f"{BACKEND_URL}/api/documents/list")
            if doc_list_res.status_code == 200:
                documents = doc_list_res.json()
        except Exception:
            pass
            
    if documents:
        doc_options = {doc["filename"]: doc["id"] for doc in documents}
        selected_doc_name = st.selectbox("Sorgulanacak Belge", options=list(doc_options.keys()))
        selected_doc_id = doc_options[selected_doc_name]
        
        if st.button("🗑️ Belgeyi Sistemden Sil", use_container_width=True):
            with st.spinner("Belge siliniyor..."):
                try:
                    del_res = requests.delete(f"{BACKEND_URL}/api/documents/{selected_doc_id}")
                    if del_res.status_code == 200:
                        st.success("Belge başarıyla silindi.")
                        st.session_state.active_heatmap = {}
                        st.session_state.selected_nodes = []
                        st.rerun()
                except Exception as e:
                    st.error(str(e))
    else:
        st.info("Sistemde henüz yüklenmiş bir belge yok.")
        selected_doc_id = None

# Ana Başlıklar
st.markdown('<h1 class="gradient-text">🛡️ Aegis RAG Arayüzü</h1>', unsafe_allow_html=True)
st.markdown('<p class="gradient-subtitle">Çift Hatlı Kesişimli Isı Haritası ve Deterministik Orkestrasyon Portalı</p>', unsafe_allow_html=True)

# Durum Değişkenleri
if "active_heatmap" not in st.session_state:
    st.session_state.active_heatmap = {}
if "selected_nodes" not in st.session_state:
    st.session_state.selected_nodes = []

# İki Sütunlu Düzen: Sol (TOC Ağacı / Heatmap), Sağ (Sohbet Odası)
main_col_left, main_col_right = st.columns([1, 2])

# Sol Sütun: TOC Ağaç ve Isı Haritası Gösterimi
with main_col_left:
    st.markdown('<div class="glass-card"><h4>🌳 Belge Hiyerarşisi & Isı Haritası</h4></div>', unsafe_allow_html=True)
    
    if selected_doc_id:
        try:
            toc_res = requests.get(f"{BACKEND_URL}/api/documents/{selected_doc_id}/toc")
            if toc_res.status_code == 200:
                toc_nodes = toc_res.json()
                
                # Ağacı Isı Haritasına göre boyama mantığı
                toc_html = ['<div style="max-height: 550px; overflow-y: auto; padding-right: 5px; background: #030508; padding: 10px; border-radius: 8px; border: 1px solid rgba(255,255,255,0.02);">']
                for node in toc_nodes:
                    n_id = node["id"]
                    level = node["level"]
                    heading = node["heading"]
                    path = node["path"]
                    
                    # Semantik skor kontrolü ve sınıf atama
                    heat_class = ""
                    score_badge = ""
                    
                    if str(n_id) in st.session_state.active_heatmap or n_id in st.session_state.active_heatmap:
                        score = st.session_state.active_heatmap.get(str(n_id), st.session_state.active_heatmap.get(n_id, 0.0))
                        if score >= 0.7:
                            heat_class = "heat-high"
                        elif score >= 0.5:
                            heat_class = "heat-medium"
                        elif score > 0.0:
                            heat_class = "heat-low"
                        score_badge = f'<span class="heat-score">%{score*100:.1f} Match</span>'
                        
                    # LLM Yönlendirme seçimi kontrolü (Active Node Border)
                    sel_class = ""
                    if n_id in st.session_state.selected_nodes:
                        sel_class = "node-selected"
                        
                    # Hiyerarşik girinti sınıfı
                    level_class = f"tree-node-{level}" if level <= 5 else "tree-node-5"
                    bullet = "📁" if level == 1 else "📄"
                    
                    toc_html.append(
                        f'<div class="tree-node-base {level_class} {heat_class} {sel_class}" title="{path}">'
                        f'<span>{bullet} {heading}</span>{score_badge}'
                        f'</div>'
                    )
                toc_html.append('</div>')
                
                st.markdown("".join(toc_html), unsafe_allow_html=True)
            else:
                st.warning("TOC yapısı alınamadı.")
        except Exception as e:
            st.error(f"TOC Ağacı bağlantı hatası: {str(e)}")
    else:
        st.info("Lütfen bir doküman seçin.")

# Sağ Sütun: Sohbet Odası
with main_col_right:
    if "messages" not in st.session_state:
        st.session_state.messages = []
        
    if not st.session_state.messages:
        st.markdown(
            """
            <div class="glass-card" style="margin-top: 10px;">
                <h4 style="color: #00f2fe; margin-top: 0;">Aegis RAG: Frontier Belge Zekası Portalına Hoş Geldiniz! 🛡️</h4>
                <p style="font-size: 0.95rem; line-height: 1.6; color: #94a3b8;">
                    Bu sistem, <strong>PageIndex</strong> hiyerarşik ağaç orkestrasyonu ile <strong>Semantik RAG</strong> katmanlarını 
                    çift hatlı paralel bir kesişim mimarisinde birleştirir.
                </p>
                <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 12px; margin-top: 15px;">
                    <div style="background: rgba(255,255,255,0.01); padding: 10px; border-radius: 8px; border: 1px solid rgba(255,255,255,0.03);">
                        <strong>⚡ Explicit Yürütme Politikası</strong><br>
                        <span style="font-size: 0.85rem; color: #64748b;">Sorgular otomatik olarak karşılaştırmalı (Multi-Subtree) veya tekil (Single-Node) modlarda işlenir.</span>
                    </div>
                    <div style="background: rgba(255,255,255,0.01); padding: 10px; border-radius: 8px; border: 1px solid rgba(255,255,255,0.03);">
                        <strong>📊 Token Ekonomisi Katmanı</strong><br>
                        <span style="font-size: 0.85rem; color: #64748b;">Bağlam penceresi 4096 token ile bütçelenir ve kesirli sıkıştırma süzgecinden geçirilir.</span>
                    </div>
                </div>
            </div>
            """, 
            unsafe_allow_html=True
        )

    # Sohbet Geçmişi
    for message in st.session_state.messages:
        if message["role"] == "user":
            st.markdown(f'<div class="chat-bubble-user">👤 <strong>Siz:</strong><br>{message["content"]}</div>', unsafe_allow_html=True)
        else:
            # Sohbet balonu ve Dashboard Rozetlerini göster
            st.markdown(f'<div class="chat-bubble-bot">🤖 <strong>Aegis RAG:</strong><br>{message["content"]}</div>', unsafe_allow_html=True)
            
            # Dashboard Gösterge Kartları
            policy = message.get("execution_policy", "Hierarchical")
            tokens = message.get("context_tokens", 0)
            fallback = "Semantik RAG" if message.get("fallback_triggered", False) else "Yapısal TOC"
            
            dashboard_html = f"""
            <div class="dashboard-container">
                <span class="dashboard-badge badge-policy">🎯 Politika: {policy}</span>
                <span class="dashboard-badge badge-token">📊 Bağlam: {tokens} / 4000 Token</span>
                <span class="dashboard-badge badge-fallback">🛡️ Arama Tipi: {fallback}</span>
            </div>
            """
            st.markdown(dashboard_html, unsafe_allow_html=True)
            
            if "trace" in message and message["trace"]:
                with st.expander("🌳 Düşünce Yolu & Orkestrasyon Karar Günlüğü (Tree Trace)", expanded=False):
                    trace_html = ['<div class="trace-container"><div class="trace-title">🛡️ Sistem Karar ve Sentez Aşamaları</div>']
                    for idx, step in enumerate(message["trace"]):
                        trace_html.append(f'<div class="trace-step">{step}</div>')
                    trace_html.append('</div>')
                    st.markdown("".join(trace_html), unsafe_allow_html=True)

    # Sohbet Girişi
    if selected_doc_id:
        user_query = st.chat_input("Belge hakkında araştırma sorusu yazın...")
        
        if user_query:
            st.session_state.messages.append({"role": "user", "content": user_query})
            st.markdown(f'<div class="chat-bubble-user">👤 <strong>Siz:</strong><br>{user_query}</div>', unsafe_allow_html=True)

            payload = {"document_id": selected_doc_id, "query": user_query, "model_name": selected_model}
            meta_box = {"meta": None}

            def stream_answer():
                """Streaming: gövdenin ilk satırı JSON meta, sonrası cevap metni (token token)."""
                resp = requests.post(f"{BACKEND_URL}/api/query/stream", json=payload, stream=True, timeout=180)
                resp.raise_for_status()
                dec = codecs.getincrementaldecoder("utf-8")()
                buf, meta_done = "", False
                for raw in resp.iter_content(chunk_size=48):
                    if not raw:
                        continue
                    text = dec.decode(raw)
                    if not text:
                        continue
                    if not meta_done:
                        buf += text
                        if "\n" in buf:
                            line, rest = buf.split("\n", 1)
                            meta_box["meta"] = json.loads(line)
                            meta_done = True
                            if rest:
                                yield rest
                    else:
                        yield text

            bot_answer, meta = None, None
            st.markdown('<div class="chat-bubble-bot">🤖 <strong>Aegis RAG</strong> akıl yürütüyor…</div>', unsafe_allow_html=True)
            try:
                bot_answer = st.write_stream(stream_answer)
                meta = meta_box["meta"] or {}
            except Exception:
                # Streaming başarısızsa tam-yanıt (JSON) endpoint'ine düş
                try:
                    with st.spinner("Aegis RAG akıl yürütüyor…"):
                        r = requests.post(f"{BACKEND_URL}/api/query", json=payload, timeout=180)
                        r.raise_for_status()
                        meta = r.json()
                        bot_answer = meta.get("answer", "Cevap üretilemedi.")
                except Exception as e:
                    st.error(f"Bağlantı hatası: {str(e)}")

            if bot_answer is not None:
                meta = meta or {}
                st.session_state.active_heatmap = meta.get("heatmap_scores", {})
                st.session_state.selected_nodes = meta.get("selected_node_ids", [])
                st.session_state.messages.append({
                    "role": "assistant",
                    "content": bot_answer,
                    "execution_policy": meta.get("execution_policy", "Recursive Descent"),
                    "context_tokens": meta.get("context_tokens", 0),
                    "fallback_triggered": meta.get("fallback_triggered", False),
                    "trace": meta.get("trace", []),
                })
                st.rerun()
    else:
        st.warning("⚠️ Sohbet edebilmek için lütfen önce sol menüden bir doküman yükleyin veya aktif bir doküman seçin.")
