"""
LLM sözleşmesi + adaptörler (Ollama, OpenAI-uyumlu local endpoint) + factory.

Core Engine bu arayüzü kullanır; `think` gibi Ollama-özel detaylar adaptör içinde kalır.
Şirket kendi local modelini `openai_compat` (vLLM / LM Studio / llama.cpp) ile takar.
"""
import json
import re
from abc import ABC, abstractmethod
from typing import Iterator, Optional

import requests

from src import config


def strip_think(text: str) -> str:
    """Thinking modellerinin (qwen3 vb.) <think>...</think> bloklarını yanıttan ayıklar."""
    return re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()


class BaseLLM(ABC):
    """Şirketin kendi LLM sunucusu bu arayüzü uygular."""

    @abstractmethod
    def generate(self, prompt: str, *, temperature: Optional[float] = None,
                 json_mode: bool = False) -> str: ...

    @abstractmethod
    def generate_stream(self, prompt: str) -> Iterator[str]: ...


class OllamaLLM(BaseLLM):
    def __init__(self, model: str = None, base_url: str = None):
        self.model = model or config.LLM_MODEL
        self.base_url = (base_url or config.LLM_BASE_URL).rstrip("/")

    def _payload(self, prompt, temperature, json_mode, stream):
        p = {"model": self.model, "prompt": prompt, "stream": stream,
             "think": config.LLM_THINKING, "keep_alive": config.OLLAMA_KEEP_ALIVE}
        if temperature is not None:
            p["options"] = {"temperature": temperature}
        if json_mode:
            p["format"] = "json"
        return p

    def generate(self, prompt, *, temperature=None, json_mode=False) -> str:
        r = requests.post(f"{self.base_url}/api/generate",
                          json=self._payload(prompt, temperature, json_mode, False),
                          timeout=config.OLLAMA_GENERATE_TIMEOUT)
        r.raise_for_status()
        return strip_think(r.json().get("response", ""))

    def generate_stream(self, prompt) -> Iterator[str]:
        # think=False → akış içinde <think> beklenmez; parça parça yield ederiz.
        with requests.post(f"{self.base_url}/api/generate",
                           json=self._payload(prompt, None, False, True),
                           stream=True, timeout=config.OLLAMA_GENERATE_TIMEOUT) as r:
            r.raise_for_status()
            for line in r.iter_lines():
                if not line:
                    continue
                try:
                    piece = json.loads(line).get("response", "")
                except Exception:
                    continue
                if piece:
                    yield piece


class OpenAICompatLLM(BaseLLM):
    """OpenAI-uyumlu `/v1/chat/completions` (vLLM, LM Studio, llama.cpp...). Yalnız requests."""

    def __init__(self, model: str = None, base_url: str = None, api_key: str = None):
        self.model = model or config.LLM_MODEL
        self.base_url = (base_url or config.LLM_BASE_URL).rstrip("/")
        self.api_key = api_key or config.LLM_API_KEY

    def _headers(self):
        return {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}

    def _body(self, prompt, temperature, json_mode, stream):
        b = {"model": self.model, "messages": [{"role": "user", "content": prompt}], "stream": stream}
        if temperature is not None:
            b["temperature"] = temperature
        if json_mode:
            b["response_format"] = {"type": "json_object"}
        return b

    def generate(self, prompt, *, temperature=None, json_mode=False) -> str:
        r = requests.post(f"{self.base_url}/chat/completions", headers=self._headers(),
                          json=self._body(prompt, temperature, json_mode, False),
                          timeout=config.OLLAMA_GENERATE_TIMEOUT)
        r.raise_for_status()
        return strip_think(r.json()["choices"][0]["message"]["content"] or "")

    def generate_stream(self, prompt) -> Iterator[str]:
        with requests.post(f"{self.base_url}/chat/completions", headers=self._headers(),
                           json=self._body(prompt, None, False, True),
                           stream=True, timeout=config.OLLAMA_GENERATE_TIMEOUT) as r:
            r.raise_for_status()
            for line in r.iter_lines(decode_unicode=True):
                if not line or not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                try:
                    delta = json.loads(data)["choices"][0]["delta"].get("content", "")
                except Exception:
                    continue
                if delta:
                    yield delta


def get_llm(model: str = None) -> BaseLLM:
    """config.LLM_PROVIDER'a göre LLM üretir. `model` verilirse o modeli kullanır."""
    provider = (config.LLM_PROVIDER or "ollama").lower()
    if provider == "openai_compat":
        return OpenAICompatLLM(model=model)
    return OllamaLLM(model=model)
