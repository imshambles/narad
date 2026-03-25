from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime


@dataclass
class RawArticle:
    title: str
    url: str
    summary: str | None
    published_at: datetime | None
    image_url: str | None
    source_name: str


class SourceAdapter(ABC):
    @abstractmethod
    async def fetch(self) -> list[RawArticle]:
        ...
