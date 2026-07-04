"""
Embedder sözleşmesi + adaptörler (Ollama, OpenAI-uyumlu local endpoint) + factory.

FAIL-FAST: model/servis erişilemezse sessizce başka modele yedeklenmez (farklı boyutlu
vektörlerin karışmasını önlemek için açık hata fırlatılır).
"""
from abc import ABC, abstractmethod
from typing import List

import requests

from src import config


class BaseEmbedder(ABC):
    """Şirketin kendi embedder'ı bu arayüzü uygular."""

    @property
    @abstractmethod
    def model_key(self) -> str:
        """Koleksiyon kimliği (model değişince otomatik yeniden kurulum için)."""

    @abstractmethod
    def embed_documents(self, texts: List[str]) -> List[List[float]]: ...

    @abstractmethod
    def embed_query(self, text: str) -> List[float]: ...


class OllamaEmbedder(BaseEmbedder):
    """
    Ollama yerel embedding modeli. nomic-embed-text görev ön-eki (search_document:/search_query:)
    ister; ön-ek gerektirmeyen modellerde (bge-m3) boş bırakılır.
    """

    def __init__(self, model: str = None, base_url: str = None):
        self.model = model or config.EMBED_MODEL
        self.base_url = (base_url or config.EMBED_BASE_URL).rstrip("/")
        uses_prefix = self.model.startswith("nomic")
        self.doc_prefix = "search_document: " if uses_prefix else ""
        self.query_prefix = "search_query: " if uses_prefix else ""

    @property
    def model_key(self) -> str:
        # Geriye uyumluluk: ollama için salt model adı (eski koleksiyonlar düşmesin).
        return self.model

    def _embed_one(self, text: str) -> List[float]:
        response = requests.post(
            f"{self.base_url}/api/embeddings",
            json={"model": self.model, "prompt": text, "keep_alive": config.OLLAMA_KEEP_ALIVE},
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


class OpenAICompatEmbedder(BaseEmbedder):
    """
    OpenAI-uyumlu local endpoint (vLLM, LM Studio, llama.cpp...) `/v1/embeddings`.
    Ekstra SDK bağımlılığı yok — yalnız requests.
    """

    def __init__(self, model: str = None, base_url: str = None, api_key: str = None):
        self.model = model or config.EMBED_MODEL
        self.base_url = (base_url or config.EMBED_BASE_URL).rstrip("/")
        self.api_key = api_key or config.EMBED_API_KEY

    @property
    def model_key(self) -> str:
        return f"openai_compat:{self.model}"

    def _embed_batch(self, texts: List[str]) -> List[List[float]]:
        response = requests.post(
            f"{self.base_url}/embeddings",
            headers={"Authorization": f"Bearer {self.api_key}"},
            json={"model": self.model, "input": texts},
            timeout=config.EMBED_TIMEOUT,
        )
        if response.status_code != 200:
            raise RuntimeError(
                f"Embedding endpoint '{self.base_url}' yanıt vermedi (HTTP {response.status_code})."
            )
        data = response.json()["data"]
        return [item["embedding"] for item in data]

    def embed_documents(self, texts: List[str]) -> List[List[float]]:
        return self._embed_batch(texts) if texts else []

    def embed_query(self, text: str) -> List[float]:
        return self._embed_batch([text])[0]


def get_embedder() -> BaseEmbedder:
    """config.EMBED_PROVIDER'a göre embedder üretir (varsayılan: ollama)."""
    provider = (config.EMBED_PROVIDER or "ollama").lower()
    if provider == "openai_compat":
        return OpenAICompatEmbedder()
    return OllamaEmbedder()
