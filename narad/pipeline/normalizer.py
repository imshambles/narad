import hashlib
from datetime import datetime, timezone

from narad.sources.base import RawArticle


def make_fingerprint(title: str, url: str) -> str:
    """Create a SHA256 fingerprint from normalized title + URL domain."""
    normalized = title.lower().strip()
    # Extract domain from URL for fingerprinting
    from urllib.parse import urlparse
    domain = urlparse(url).netloc
    content = f"{normalized}|{domain}"
    return hashlib.sha256(content.encode()).hexdigest()


def normalize_article(raw: RawArticle) -> dict:
    """Convert a RawArticle to a dict ready for DB insertion."""
    published = raw.published_at or datetime.now(timezone.utc)
    # Ensure UTC
    if published.tzinfo is None:
        published = published.replace(tzinfo=timezone.utc)

    return {
        "title": raw.title.strip(),
        "summary": raw.summary,
        "external_url": raw.url,
        "published_at": published,
        "fingerprint": make_fingerprint(raw.title, raw.url),
        "image_url": raw.image_url,
        "source_name": raw.source_name,
    }
