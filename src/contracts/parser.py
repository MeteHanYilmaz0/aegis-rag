"""
Parser sözleşmesi: RawNode (ara temsil / IR) + BaseParser + ParserRegistry.

Şirket kendi parser'ını `BaseParser`'ı uygulayan tek bir sınıfla yazar; `RawNode` listesi
üretir (heading/level/content/sayfa). Motor gerisini halleder (id/parent/path + finalize).
Motor hiçbir somut parser adı bilmez; yalnız bu arayüzü kullanır.
"""
import importlib
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import List, Optional


@dataclass
class RawNode:
    """Her parser'ın üretmek ZORUNDA olduğu ara temsil. Motorun tek girdisi."""
    heading: str
    level: int                       # 1 = en üst
    content: str = ""                # bu başlığın altındaki ham metin
    start_page: Optional[int] = None
    end_page: Optional[int] = None


class BaseParser(ABC):
    name: str = "base"

    @abstractmethod
    def parse(self, file_path: str, progress_cb=None) -> List[RawNode]:
        """Başlık bulunamazsa/uygulanamazsa BOŞ liste döndür (çağıran bir sonrakini dener)."""

    def supports(self, file_path: str) -> bool:
        return file_path.lower().endswith(".pdf")


_REGISTRY = {}


def register_parser(name: str, cls):
    _REGISTRY[name] = cls


def get_parser(name: str) -> BaseParser:
    """
    Kayıtlı parser'ı ada göre üretir. "paket.modul:Sinif" biçiminde harici sınıf da
    import edebilir → şirket parser'ı tek dosya yazıp config.PARSER'a adını koyar.
    """
    if name in _REGISTRY:
        return _REGISTRY[name]()
    if ":" in name:
        module_path, class_name = name.split(":", 1)
        cls = getattr(importlib.import_module(module_path), class_name)
        return cls()
    raise KeyError(f"Bilinmeyen parser: {name!r}. Kayıtlı: {list(_REGISTRY)} veya 'modul:Sinif'.")
