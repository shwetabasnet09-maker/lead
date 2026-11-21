"""Adapters for sources that publish an official, free API.

These sources are governed by the provider's API terms rather than robots.txt, so they
skip the robots check but still require the same permission review as any other source.
Configuration lives in Source.selectors, for example:

    Mastodon: {"hashtags": ["ndis", "disability"]}
    Bluesky / Brave: {"queries": ["NDIS \"looking for\" support worker {location}"]}

A query containing {location} runs once per search-profile location. A query containing
{service} runs once per entry in the optional "services" list (default SERVICE_PHRASES).
"""
from __future__ import annotations

from urllib.parse import quote, urljoin

from django.conf import settings

from .classifier import INTENT_TERMS, _contains, _contains_word
from .models import Keyword, SearchProfile, ServiceCategory, Source
from .scrapers import ScrapedRecord, ScraperError, _parse_date, _session, _strip_html

API_SOURCE_TYPES = {Source.SourceType.MASTODON, Source.SourceType.BLUESKY, Source.SourceType.BRAVE}

# The services the business offers, phrased the way people write them in a request.
SERVICE_PHRASES = [
    "supported independent living",
    "short term accommodation",
    "assistance with daily living",
    "community participation",
    "life skills",
    "transport",
    "support coordinator",
    "personal care",
    "cleaning",
    "occupational therapist",
    "behaviour support",
    "therapy assistant",
]


def _title_from_text(text: str) -> str:
    first_line = next((line.strip() for line in text.splitlines() if line.strip()), text.strip())
    return first_line[:140] + ("…" if len(first_line) > 140 else "")


def _is_service_request(record: ScrapedRecord, keywords: list[str]) -> bool:
    text = f"{record.title} {record.snippet}".lower()
    return _contains(text, INTENT_TERMS) and _contains_word(text, keywords)


def service_keywords(profile: SearchProfile) -> list[str]:
    """Every term that names a service: the profile's keywords and each category's synonyms."""
    keywords = list(profile.keywords.filter(kind=Keyword.Kind.POSITIVE, enabled=True).values_list("term", flat=True))
    for category in ServiceCategory.objects.filter(active=True):
        keywords.extend(category.terms())
    return keywords


def is_service_request(record: ScrapedRecord, keywords: list[str]) -> bool:
    return _is_service_request(record, keywords)


def api_requests(source: Source, profile: SearchProfile) -> list[str]:
    """Expand the source's configured hashtags or queries into individual API requests."""
    config = source.selectors or {}
    items = config.get("hashtags") if source.source_type == Source.SourceType.MASTODON else config.get("queries")
    if not items:
        key = "hashtags" if source.source_type == Source.SourceType.MASTODON else "queries"
        raise ScraperError(f"{source.get_source_type_display()} source requires a '{key}' list in its configuration")
    locations = profile.location_terms() or [""]
    services = config.get("services") or SERVICE_PHRASES
    expanded: list[str] = []
    for item in items:
        variants = [item.replace("{service}", service) for service in services] if "{service}" in item else [item]
        for variant in variants:
            if "{location}" in variant:
                expanded.extend(variant.replace("{location}", location).strip() for location in locations)
            else:
                expanded.append(variant)
    return list(dict.fromkeys(expanded))


def fetch_mastodon(source: Source, hashtag: str) -> list[ScrapedRecord]:
    url = urljoin(source.base_url, f"/api/v1/timelines/tag/{quote(hashtag.lstrip('#'))}")
    response = _session().get(url, params={"limit": 40}, timeout=settings.SCRAPER_TIMEOUT_SECONDS)
    response.raise_for_status()
    records = []
    for status in response.json():
        if status.get("visibility") != "public" or not status.get("url"):
            continue
        text = _strip_html(status.get("content", ""))
        if not text:
            continue
        account = status.get("account") or {}
        records.append(
            ScrapedRecord(
                title=_title_from_text(text),
                url=status["url"],
                snippet=text[:1000],
                published_at=_parse_date(status.get("created_at")),
                public_name=account.get("display_name") or account.get("acct", ""),
                external_id=status.get("id", ""),
            )
        )
    return records


BLUESKY_PUBLIC_API = "https://api.bsky.app/"


def _bluesky_token(source: Source) -> str:
    """A login token, or "" to use Bluesky's public search, which needs no account."""
    if not settings.BLUESKY_HANDLE or not settings.BLUESKY_APP_PASSWORD:
        return ""
    response = _session().post(
        urljoin(source.base_url, "/xrpc/com.atproto.server.createSession"),
        json={"identifier": settings.BLUESKY_HANDLE, "password": settings.BLUESKY_APP_PASSWORD},
        timeout=settings.SCRAPER_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    return response.json()["accessJwt"]


def fetch_bluesky(source: Source, query: str, token: str) -> list[ScrapedRecord]:
    response = _session().get(
        urljoin(source.base_url if token else BLUESKY_PUBLIC_API, "/xrpc/app.bsky.feed.searchPosts"),
        params={"q": query, "limit": 50, "sort": "latest"},
        headers={"Authorization": f"Bearer {token}"} if token else {},
        timeout=settings.SCRAPER_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    records = []
    for post in response.json().get("posts", []):
        record = post.get("record") or {}
        author = post.get("author") or {}
        text = record.get("text", "").strip()
        uri = post.get("uri", "")
        if not text or "/app.bsky.feed.post/" not in uri:
            continue
        rkey = uri.rsplit("/", 1)[-1]
        records.append(
            ScrapedRecord(
                title=_title_from_text(text),
                url=f"https://bsky.app/profile/{author.get('handle') or author.get('did')}/post/{rkey}",
                snippet=text[:1000],
                published_at=_parse_date(record.get("createdAt")),
                public_name=author.get("displayName") or author.get("handle", ""),
                external_id=uri,
            )
        )
    return records


def _brave_freshness(days: int) -> str:
    if days <= 1:
        return "pd"
    if days <= 7:
        return "pw"
    return "pm" if days <= 31 else "py"


def fetch_brave(source: Source, query: str, freshness_days: int = 7) -> list[ScrapedRecord]:
    if not settings.BRAVE_API_KEY:
        raise ScraperError("Set BRAVE_API_KEY to use the Brave Search source")
    response = _session().get(
        urljoin(source.base_url, "/res/v1/web/search"),
        params={"q": query, "country": "AU", "count": 20, "freshness": _brave_freshness(freshness_days)},
        headers={
            "Accept": "application/json",
            "Cache-Control": "no-cache",
            "X-Subscription-Token": settings.BRAVE_API_KEY,
        },
        timeout=settings.SCRAPER_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    records = []
    for result in (response.json().get("web") or {}).get("results", []):
        if not result.get("title") or not result.get("url"):
            continue
        records.append(
            ScrapedRecord(
                title=_strip_html(result["title"]),
                url=result["url"],
                snippet=_strip_html(result.get("description", "")),
                published_at=_parse_date(result.get("page_age")),
            )
        )
    return records


def fetch_api_source(source: Source, profile: SearchProfile):
    """Yield (request, records) pairs for each configured hashtag or query.

    Only records with both service-seeking language and a service keyword are kept, so
    hashtags and web searches do not fill the CRM with news and general discussion.
    """
    if not source.is_runnable:
        raise ScraperError("Source is disabled or has not completed permission review")
    token = _bluesky_token(source) if source.source_type == Source.SourceType.BLUESKY else ""
    keywords = service_keywords(profile)
    for item in api_requests(source, profile):
        if source.source_type == Source.SourceType.MASTODON:
            records = fetch_mastodon(source, item)
        elif source.source_type == Source.SourceType.BLUESKY:
            records = fetch_bluesky(source, item, token)
        else:
            records = fetch_brave(source, item, profile.freshness_days)
        yield item, [record for record in records if _is_service_request(record, keywords)]
