"""
Model-bağımsız lexical-semantik yeniden sıralayıcı (reranker).

Lexical hat, saf kelime-kesişimi yerine:
  • BM25 (IDF'li; aday kümesinden document-frequency) — nadir/ayırt edici kelimeleri öne çıkarır,
  • karakter n-gram Jaccard — eklemeli dillerde (Türkçe) "arşivinden"≈"arşiv" gibi kök eşleşmesi,
    stemmer bağımlılığı olmadan, dil-bağımsız.
Bu skor, ChromaDB kosinüs benzerliğiyle harmanlanır. Embedding modelinden bağımsızdır.
"""
import math
import re
import unicodedata
from typing import Any, Dict, List

from src import config


def _fold(s: str) -> str:
    """Türkçe-güvenli küçültme + aksan sadeleştirme (İ→i, ş→s...)."""
    s = unicodedata.normalize("NFKD", s or "")
    return "".join(c for c in s if not unicodedata.combining(c)).lower()


def _tokens(s: str) -> List[str]:
    return re.findall(r"\w+", _fold(s))


def char_ngrams(s: str, n: int = None) -> set:
    n = n or config.LEXICAL_NGRAM_N
    t = re.sub(r"\s+", " ", _fold(s)).strip()
    if len(t) < n:
        return {t} if t else set()
    return {t[i:i + n] for i in range(len(t) - n + 1)}


def _containment(q_grams: set, doc_grams: set) -> float:
    """Sorgunun ne kadarı belgede geçiyor (retrieval'da belge uzunluğuna dayanıklı)."""
    if not q_grams or not doc_grams:
        return 0.0
    return len(q_grams & doc_grams) / len(q_grams)


def lexical_scores(query: str, docs: List[str]) -> List[float]:
    """Her doküman için [0,1] lexical skor (BM25-normalize + karakter n-gram karışımı)."""
    if not docs:
        return []
    q_tokens = set(_tokens(query))
    q_grams = char_ngrams(query)
    doc_tokens = [_tokens(d) for d in docs]
    n = len(docs)

    df: Dict[str, int] = {}
    for dt in doc_tokens:
        for term in set(dt):
            df[term] = df.get(term, 0) + 1
    avgdl = (sum(len(dt) for dt in doc_tokens) / n) or 1.0
    k1, b = config.BM25_K1, config.BM25_B

    bm25 = []
    for dt in doc_tokens:
        dl = len(dt) or 1
        tf: Dict[str, int] = {}
        for w in dt:
            tf[w] = tf.get(w, 0) + 1
        score = 0.0
        for term in q_tokens:
            f = tf.get(term, 0)
            if not f:
                continue
            idf = math.log(1 + (n - df[term] + 0.5) / (df[term] + 0.5))
            score += idf * (f * (k1 + 1)) / (f + k1 * (1 - b + b * dl / avgdl))
        bm25.append(score)

    mx = max(bm25) if bm25 else 0.0
    out = []
    for i, doc in enumerate(docs):
        bm25n = (bm25[i] / mx) if mx > 0 else 0.0
        ng = _containment(q_grams, char_ngrams(doc))
        out.append(config.LEXICAL_BM25_WEIGHT * bm25n + config.LEXICAL_NGRAM_WEIGHT * ng)
    return out


def rerank(query: str, candidates: List[Dict[str, Any]], top_k: int) -> List[Dict[str, Any]]:
    """Adaylara `hybrid_score` = cosine*w + lexical*w atar ve sıralar."""
    if not candidates:
        return []
    lex = lexical_scores(query, [c["content"] for c in candidates])
    for cand, lx in zip(candidates, lex):
        cand["hybrid_score"] = round(
            config.RERANK_COSINE_WEIGHT * cand.get("similarity", 0.0)
            + config.RERANK_LEXICAL_WEIGHT * lx,
            4,
        )
    return sorted(candidates, key=lambda x: x["hybrid_score"], reverse=True)[:top_k]
