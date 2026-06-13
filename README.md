# Aegis RAG: Yerel Chandra-PageIndex Hibrit RAG Sistemi

Aegis RAG; tamamen yerel donanımda çalışan, veri gizliliğini %100 koruyan, sıfır bulut maliyetli ve yüksek doğruluk oranına sahip yenilikçi bir **Vektörsüz/Akıl Yürütme Tabanlı RAG** sistemidir. 

Bu sistem, **Chandra OCR 2**'nin görsel/tablo-duyarlı Markdown dönüştürme yeteneği ile **PageIndex**'in hiyerarşik ağaç navigasyonu mantığını birleştirir.

## Özellikler

- **%100 Yerel ve Gizli:** Hiçbir veri bilgisayarınızın dışına çıkmaz.
- **Kusursuz Tablo & Düzen Koruma:** Chandra OCR 2 sayesinde tablolarınız, dipnotlarınız ve biçimleriniz bozulmadan okunur.
- **Düzleştirilmiş Ağaç Seçimi (Flat-Tree Selection):** 5 katmanlı derin hiyerarşide, yerel 7B dil modellerini yormadan, tek adımda nokta atışı navigasyon yapar.
- **RAG Fallback:** Eğer hiyerarşik ağaç araması aradığınız cevaba ulaşamazsa, sistem otomatik olarak geleneksel vektör aramasına (ChromaDB) geçerek güvenliği sağlar.
- **Kolay Arayüz:** Streamlit tabanlı, düşünce yolunu (Tree Trace) şeffaf şekilde gösteren premium sohbet arayüzü.

## Proje Klasör Yapısı

```text
aegis-rag/
├── src/
│   ├── parser/         # Kural tabanlı Markdown-TOC ağaç çıkarıcı modülü
│   ├── database/       # SQLite ve ChromaDB veritabanı yönetim modülleri
│   ├── backend/        # FastAPI sunucusu ve asenkron arka plan görevleri
│   └── ui/             # Streamlit kullanıcı arayüzü
├── requirements.txt    # Python bağımlılıkları
└── README.md           # Proje kılavuzu
```

## Kurulum ve Çalıştırma

### 1. Bağımlılıkların Yüklenmesi
Yerel Python ortamınızda bağımlılıkları kurun:
```bash
pip install -r requirements.txt
```

### 2. Ollama Kurulumu ve Model İndirme
1. [Ollama.com](https://ollama.com) adresinden Ollama'yı yerel işletim sisteminize indirin ve kurun.
2. Terminalden Qwen 3.6 veya Llama 3 modelini indirin:
   ```bash
   ollama pull qwen2.5:7b-instruct  # veya qwen3.6 modeli
   ```

### 3. Uygulamayı Başlatma
1. Backend sunucusunu başlatın:
   ```bash
   uvicorn src.backend.main:app --reload --port 8000
   ```
2. Arayüzü başlatın:
   ```bash
   streamlit run src/ui/app.py
   ```
Uygulama otomatik olarak tarayıcınızda açılacaktır.
