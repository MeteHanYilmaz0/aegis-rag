"""
Aegis RAG — Golden-Set Değerlendirme (gerçek retrieval + cevap doğruluğu).

Bu, eski mock benchmark DEĞİLDİR. Çalışan backend'e gerçek sorular sorar, dönen cevabı
altın-set beklentileriyle (beklenen/yasak alt-dizgeler) kıyaslar; gecikme, yürütme
politikası ve token kullanımını ölçer, dürüst bir Markdown raporu üretir.

Önkoşul:
  1. Backend çalışıyor:  python -m uvicorn src.backend.main:app --port 8002
  2. Belgeler indekslenmiş ve tests/golden_set.json kendi belge id'lerin/sorularınla dolu.

Çalıştırma:
  python tests/benchmark_suite.py
"""
import json
import os
import time
import requests

BACKEND = os.getenv("AEGIS_BACKEND_URL", "http://127.0.0.1:8002")
GOLDEN_PATH = os.path.join(os.path.dirname(__file__), "golden_set.json")
REPORT_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "aegis_benchmark_report.md"))


def _norm(s: str) -> str:
    return " ".join((s or "").lower().split())


def run_case(case: dict) -> dict:
    payload = {
        "document_id": case["document_id"],
        "query": case["query"],
        "model_name": case.get("model_name", "qwen3:8b"),
    }
    t0 = time.perf_counter()
    try:
        r = requests.post(f"{BACKEND}/api/query", json=payload, timeout=180)
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        return {"query": case["query"], "passed": False, "latency": time.perf_counter() - t0,
                "error": str(e)}

    latency = time.perf_counter() - t0
    answer = _norm(data.get("answer", ""))
    expect = case.get("expect_any", [])   # bunlardan en az biri cevapta GEÇMELİ
    forbid = case.get("forbid_any", [])   # bunların hiçbiri cevapta GEÇMEMELİ
    hit = (any(_norm(e) in answer for e in expect)) if expect else True
    bad = any(_norm(f) in answer for f in forbid) if forbid else False
    return {
        "query": case["query"],
        "passed": hit and not bad,
        "hit": hit,
        "forbidden_present": bad,
        "latency": latency,
        "policy": data.get("execution_policy", "-"),
        "fallback": data.get("fallback_triggered"),
        "tokens": data.get("context_tokens"),
        "answer": data.get("answer", ""),
    }


def write_report(results: list):
    n = len(results)
    passed = sum(1 for r in results if r.get("passed"))
    lats = [r["latency"] for r in results if r.get("latency") is not None]
    avg = sum(lats) / len(lats) if lats else 0.0
    fb = sum(1 for r in results if r.get("fallback"))

    out = ["# Aegis RAG — Golden-Set Değerlendirme Raporu", ""]
    out.append(f"- **Soru sayısı:** {n}")
    out.append(f"- **Geçen:** {passed}/{n}" + (f" (%{100 * passed / n:.0f})" if n else ""))
    out.append(f"- **Ortalama gecikme:** {avg:.1f} s")
    out.append(f"- **Fallback'e düşen:** {fb}/{n}")
    out.append("")
    out.append("| Soru | Sonuç | Gecikme | Politika | Token |")
    out.append("|---|---|---|---|---|")
    for r in results:
        mark = "✅" if r.get("passed") else ("⚠️ HATA" if r.get("error") else "❌")
        out.append(f"| {r['query'][:55]} | {mark} | {r.get('latency', 0):.1f}s "
                   f"| {r.get('policy', '-')} | {r.get('tokens', '-')} |")
    out.append("")
    out.append("> Not: Geçme kriteri = `expect_any`'den en az biri cevapta var VE `forbid_any`'den "
               "hiçbiri yok. Beklentiler `tests/golden_set.json`'da tanımlıdır.")
    with open(REPORT_PATH, "w", encoding="utf-8") as f:
        f.write("\n".join(out))
    return passed, n, avg


def main():
    if not os.path.exists(GOLDEN_PATH):
        print(f"[!] {GOLDEN_PATH} yok. Örnek için golden_set.json oluşturun.")
        return
    with open(GOLDEN_PATH, encoding="utf-8") as f:
        cases = json.load(f)
    if not cases:
        print("[!] golden_set.json boş.")
        return

    print(f"[Aegis Golden-Set] {len(cases)} soru, backend={BACKEND}\n")
    results = []
    for i, c in enumerate(cases, 1):
        res = run_case(c)
        results.append(res)
        mark = "OK " if res.get("passed") else ("ERR" if res.get("error") else "FAIL")
        print(f"  [{i}/{len(cases)}] {mark} ({res.get('latency', 0):.1f}s) {c['query'][:60]}")

    passed, n, avg = write_report(results)
    print(f"\n[Sonuç] {passed}/{n} geçti | ort. {avg:.1f}s | rapor: aegis_benchmark_report.md")


if __name__ == "__main__":
    main()
