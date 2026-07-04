"""
Aegis RAG — Golden-Set Değerlendirme + ABLATION (Aegis vs saf RAG).

Çalışan backend'e gerçek sorular sorar, cevabı altın-set beklentileriyle (expect_any/forbid_any)
kıyaslar; opsiyonel `expected_node_ids` ile routing isabetini ölçer. `--ablation` ile aynı seti
iki kez koşar (tam motor vs `ablation=flat_rag` = ağaç/descent kapalı saf RAG) ve farkı raporlar
— "Aegis vs saf RAG" satış/paper kanıtı.

Önkoşul: backend çalışıyor (port 8002) + belgeler indeksli + tests/golden_set.json dolu.
Kullanım:
    python tests/benchmark_suite.py               # tam motor
    python tests/benchmark_suite.py --ablation     # tam motor vs saf RAG karşılaştırması
"""
import json
import os
import sys
import time
import requests

BACKEND = os.getenv("AEGIS_BACKEND_URL", "http://127.0.0.1:8002")
GOLDEN_PATH = os.path.join(os.path.dirname(__file__), "golden_set.json")
REPORT_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "aegis_benchmark_report.md"))


def _norm(s: str) -> str:
    return " ".join((s or "").lower().split())


def run_case(case: dict, ablation: str = None) -> dict:
    payload = {"document_id": case["document_id"], "query": case["query"],
               "model_name": case.get("model_name", "qwen3:8b")}
    if ablation:
        payload["ablation"] = ablation
    t0 = time.perf_counter()
    try:
        r = requests.post(f"{BACKEND}/api/query", json=payload, timeout=180)
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        return {"passed": False, "latency": time.perf_counter() - t0, "error": str(e)}

    latency = time.perf_counter() - t0
    answer = _norm(data.get("answer", ""))
    expect = case.get("expect_any", [])
    forbid = case.get("forbid_any", [])
    hit = any(_norm(e) in answer for e in expect) if expect else True
    bad = any(_norm(f) in answer for f in forbid) if forbid else False

    routing = None
    if case.get("expected_node_ids"):
        selected = set(data.get("selected_node_ids", []))
        routing = bool(selected & set(case["expected_node_ids"]))

    return {"passed": hit and not bad, "latency": latency, "routing_hit": routing,
            "policy": data.get("execution_policy", "-"), "tokens": data.get("context_tokens"),
            "fallback": data.get("fallback_triggered")}


def _summary(results):
    n = len(results)
    passed = sum(1 for r in results if r.get("passed"))
    routed = [r for r in results if r.get("routing_hit") is not None]
    routing_ok = sum(1 for r in routed if r["routing_hit"])
    lats = [r["latency"] for r in results if r.get("latency") is not None]
    return {
        "n": n, "passed": passed,
        "pass_rate": (100 * passed / n) if n else 0,
        "routing": f"{routing_ok}/{len(routed)}" if routed else "—",
        "avg_latency": (sum(lats) / len(lats)) if lats else 0,
    }


def main():
    ablation = "--ablation" in sys.argv
    if not os.path.exists(GOLDEN_PATH):
        print(f"[!] {GOLDEN_PATH} yok."); return
    cases = json.load(open(GOLDEN_PATH, encoding="utf-8"))
    if not cases:
        print("[!] golden_set.json boş."); return

    print(f"[Aegis Golden-Set] {len(cases)} soru, backend={BACKEND}, ablation={ablation}\n")
    full, flat = [], []
    for i, c in enumerate(cases, 1):
        r_full = run_case(c, ablation=None)
        full.append(r_full)
        line = f"  [{i}/{len(cases)}] tam={'OK ' if r_full.get('passed') else 'FAIL'}"
        if ablation:
            r_flat = run_case(c, ablation="flat_rag")
            flat.append(r_flat)
            line += f" | saf_rag={'OK ' if r_flat.get('passed') else 'FAIL'}"
        print(line + f"  {c['query'][:55]}")

    sf = _summary(full)
    out = ["# Aegis RAG — Golden-Set Değerlendirme Raporu", ""]
    out.append(f"- **Soru:** {sf['n']}  |  **Geçen (tam motor):** {sf['passed']}/{sf['n']} (%{sf['pass_rate']:.0f})")
    out.append(f"- **Routing isabeti:** {sf['routing']}  |  **Ort. gecikme:** {sf['avg_latency']:.1f}s")
    if ablation:
        sr = _summary(flat)
        out += ["", "## Ablation: Tam Motor vs Saf RAG", "",
                "| Mod | Geçen | % | Ort. gecikme |", "|---|---|---|---|",
                f"| **Aegis (tam motor)** | {sf['passed']}/{sf['n']} | %{sf['pass_rate']:.0f} | {sf['avg_latency']:.1f}s |",
                f"| Saf RAG (ağaç/descent kapalı) | {sr['passed']}/{sr['n']} | %{sr['pass_rate']:.0f} | {sr['avg_latency']:.1f}s |",
                "", f"> **Fark:** Aegis, saf RAG'a göre **+{sf['pass_rate'] - sr['pass_rate']:.0f} puan** doğruluk."]
    open(REPORT_PATH, "w", encoding="utf-8").write("\n".join(out))
    print(f"\n[Rapor] aegis_benchmark_report.md")
    if ablation:
        print(f"[Ablation] Aegis %{sf['pass_rate']:.0f} vs saf RAG %{_summary(flat)['pass_rate']:.0f}")


if __name__ == "__main__":
    main()
