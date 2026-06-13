@echo off
title Aegis RAG Launcher
echo ===================================================
echo  🛡️  Aegis Knowledge OS - Launcher
echo ===================================================
echo.

:: 1. Ollama Servis Kontrolü
echo [1/3] Ollama servis durumu kontrol ediliyor...
curl -s http://localhost:11434/api/tags > nul
if %errorlevel% neq 0 (
    echo [!] Ollama calismiyor! Ollama baslatiliyor...
    start "" "ollama"
    echo Ollama'nin yuklenmesi icin 4 saniye bekleniyor...
    timeout /t 4 > nul
) else (
    echo 🟢 Ollama calisiyor ve hazir.
)

:: 2. FastAPI Backend Başlatma (Yeni pencerede)
echo [2/3] FastAPI Backend (Uvicorn) yeni pencerede baslatiliyor...
start "Aegis RAG - Backend API" cmd /k "cd /d %~dp0 && python -m uvicorn src.backend.main:app --reload --port 8002"

:: Backend'in ayağa kalkması için kısa bekleme
timeout /t 2 > nul

:: 3. Streamlit Arayüz Başlatma (Yeni pencerede)
echo [3/3] Streamlit Kullanici Arayuzu yeni pencerede baslatiliyor...
start "Aegis RAG - Streamlit UI" cmd /k "cd /d %~dp0 && streamlit run src/ui/app.py"

echo.
echo ===================================================
echo  🟢 Aegis RAG Basariyla Tetiklendi!
echo  - Backend API: http://127.0.0.1:8002
echo  - Arayuz (UI): http://localhost:8501
echo ===================================================
echo.
pause
