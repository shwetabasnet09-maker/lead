"""Groq, an alternative to Gemini using open-source models: post scoring and the AI search page.

The scoring prompt, schema and validation are shared with Gemini so that both providers
behave the same way. When GROQ_API_KEY is set, Groq is used for both jobs.
"""
from __future__ import annotations

import json
import logging
import re
import time

import requests
from django.conf import settings

from .gemini import Judgement, LiveSearchResult, _judge_prompt, _judge_schema, _search_prompt, _validated_judgement
from .models import SearchProfile, ServiceCategory
from .scrapers import ScrapedRecord

logger = logging.getLogger(__name__)

PROVIDER_NAME = "Groq"
API_URL = "https://api.groq.com/openai/v1/chat/completions"
JUDGE_TIMEOUT_SECONDS = 30
SEARCH_TIMEOUT_SECONDS = 90
MAX_RATE_LIMIT_WAIT_SECONDS = 60
# A search makes several model calls and restarts from nothing if retried. The retry-after
# value only covers the one call that was refused, so give the per-minute limit time to refill.
SEARCH_MIN_RETRY_WAIT_SECONDS = 30

_CITATION_MARKER = re.compile(r"【[^】]*】")
_URL = re.compile(r"https?://[^\s<>\"'\]\)】]+")


class GroqError(RuntimeError):
    pass


def is_configured() -> bool:
    return bool(settings.GROQ_API_KEY)


def _retry_after(response: requests.Response) -> int:
    try:
        return max(0, int(response.headers.get("retry-after", 5)))
    except ValueError:
        return 5


def _chat(body: dict, timeout: int, min_retry_wait: int = 0) -> dict:
    headers = {"Authorization": f"Bearer {settings.GROQ_API_KEY}", "Content-Type": "application/json"}
    try:
        response = requests.post(API_URL, headers=headers, json=body, timeout=timeout)
        # A long wait means the daily limit has run out, so retrying soon cannot succeed.
        if response.status_code == 429 and _retry_after(response) <= MAX_RATE_LIMIT_WAIT_SECONDS:
            time.sleep(max(_retry_after(response), min_retry_wait))
            response = requests.post(API_URL, headers=headers, json=body, timeout=timeout)
    except requests.RequestException as exc:
        raise GroqError(f"Could not reach Groq: {type(exc).__name__}") from exc
    if response.status_code == 429:
        if _retry_after(response) > MAX_RATE_LIMIT_WAIT_SECONDS:
            raise GroqError("Groq's usage limit has been reached for now. Please try again later.")
        raise GroqError("Groq is busy because of its per-minute limit. Please wait a minute and try again.")
    try:
        data = response.json()
    except ValueError:
        data = {}
    if not response.ok:
        message = (data.get("error") or {}).get("message", "") if isinstance(data, dict) else ""
        raise GroqError(f"Groq returned HTTP {response.status_code}: {message}".rstrip(": "))
    return data


def judge_record(record: ScrapedRecord, profile: SearchProfile) -> Judgement | None:
    """Score one post. Returns None on any failure so that the rule-based result stands."""
    categories = {category.name: category for category in ServiceCategory.objects.filter(active=True)}
    schema = _judge_schema(list(categories))
    body = {
        "model": settings.GROQ_MODEL,
        "temperature": 0,
        "messages": [{"role": "user", "content": _judge_prompt(record, profile)}],
        "response_format": {
            "type": "json_schema",
            # Groq's strict mode requires additionalProperties to be false on every object.
            "json_schema": {"name": "lead_judgement", "strict": True, "schema": {**schema, "additionalProperties": False}},
        },
    }
    try:
        try:
            data = _chat(body, JUDGE_TIMEOUT_SECONDS)
        except GroqError as exc:
            # The model occasionally writes a value outside the schema and Groq rejects it.
            # A second attempt usually succeeds.
            if "does not match the expected schema" not in str(exc):
                raise
            data = _chat(body, JUDGE_TIMEOUT_SECONDS)
        return _validated_judgement(json.loads(data["choices"][0]["message"]["content"]), categories)
    except (GroqError, ValueError, TypeError, AttributeError, KeyError, IndexError) as exc:
        # Do not log the post itself: it can describe a real person.
        logger.warning("Groq could not score a post, keeping the rule-based result: %s", exc)
        return None


def _normalise_url(url: str) -> str:
    return url.rstrip(".,;:/")


def search_for_leads(service: str, location: str, days: int) -> LiveSearchResult:
    """Run a live web search for the staff member viewing the page."""
    prompt = _search_prompt(service, location, days) + " Give the full URL of each post."
    body = {
        "model": settings.GROQ_MODEL,
        "temperature": 0.2,
        "max_completion_tokens": 2048,
        "reasoning_effort": "low",
        "tool_choice": "required",
        "tools": [{"type": "browser_search"}],
        "messages": [{"role": "user", "content": prompt}],
    }
    data = _chat(body, SEARCH_TIMEOUT_SECONDS, min_retry_wait=SEARCH_MIN_RETRY_WAIT_SECONDS)
    try:
        message = data["choices"][0]["message"]
    except (KeyError, IndexError, TypeError) as exc:
        raise GroqError("Groq returned an unexpected answer. Please try again.") from exc

    searched = {}
    for tool in message.get("executed_tools") or []:
        for result in (tool.get("search_results") or {}).get("results") or []:
            url = result.get("url") or ""
            if url.startswith(("https://", "http://")):
                searched.setdefault(_normalise_url(url), {"url": url, "title": result.get("title") or url})

    # Models can invent links. Only a link that the search really returned may be shown.
    citations = {}

    def keep_real_link(match: re.Match) -> str:
        key = _normalise_url(match.group(0))
        if key not in searched:
            return "[link removed: not in the search results]"
        citations[key] = searched[key]
        return match.group(0)

    text = _URL.sub(keep_real_link, _CITATION_MARKER.sub("", message.get("content") or "")).strip()
    if not text:
        raise GroqError("Groq returned an empty answer. Please try again.")
    return LiveSearchResult(text=text, citations=list(citations.values()))
