from datetime import datetime

from pydantic import BaseModel


class ArticleOut(BaseModel):
    id: int
    title: str
    summary: str | None
    external_url: str
    published_at: datetime
    source_name: str
    image_url: str | None

    model_config = {"from_attributes": True}


class SourceOut(BaseModel):
    id: int
    name: str
    source_type: str
    is_active: bool
    last_fetched_at: datetime | None

    model_config = {"from_attributes": True}


class EventOut(BaseModel):
    id: int
    title: str
    summary: str | None
    category: str | None
    article_count: int
    source_count: int
    first_seen_at: datetime
    last_updated_at: datetime
    entities: list[dict] | None = None

    model_config = {"from_attributes": True}


class RelatedEventOut(BaseModel):
    event_id: int
    title: str
    relationship_type: str
    shared_entities: list[str] | None = None
    weight: float


class EventDetailOut(EventOut):
    key_facts: list[str] | None = None
    disagreements: list[str] | None = None
    articles: list[ArticleOut] = []
    related_events: list[RelatedEventOut] = []


class GraphNodeOut(BaseModel):
    id: int
    title: str
    category: str | None
    article_count: int


class GraphEdgeOut(BaseModel):
    source: int
    target: int
    relationship_type: str
    weight: float
    shared_entities: list[str] | None = None


class GraphOut(BaseModel):
    nodes: list[GraphNodeOut]
    edges: list[GraphEdgeOut]
