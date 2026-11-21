from __future__ import annotations

import json
import os
from pathlib import Path
from datetime import timedelta

import requests
from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.urls import reverse
from django.utils import timezone

from . import gemini, groq
from .api_sources import API_SOURCE_TYPES, api_requests, fetch_api_source, is_service_request, service_keywords
from .classifier import classify_record, find_possible_duplicate
from .models import (
    AIConfiguration,
    AuditLog,
    Lead,
    Notification,
    Organisation,
    ScrapeRun,
    SearchProfile,
    ServiceCategory,
    Source,
)
from .scrapers import ScrapedRecord, build_search_url, open_browser, polite_pause, scrape_source


def default_organisation() -> Organisation:
    organisation, _ = Organisation.objects.get_or_create(slug="default", defaults={"name": "NDIS Lead Finder"})
    return organisation


def active_profile(organisation: Organisation) -> SearchProfile | None:
    return SearchProfile.objects.filter(organisation=organisation, active=True).order_by("id").first()


def freshness_days_for(organisation: Organisation) -> int:
    profile = active_profile(organisation)
    return profile.freshness_days if profile else SearchProfile._meta.get_field("freshness_days").default


def recent_leads_q(days: int) -> Q:
    """Leads posted inside the window. Undated leads count from the day they were found."""
    cutoff = timezone.now() - timedelta(days=days)
    return Q(published_at__gte=cutoff) | Q(published_at__isnull=True, discovered_at__gte=cutoff)


def is_stale(record: ScrapedRecord, profile: SearchProfile) -> bool:
    return bool(record.published_at and record.published_at < timezone.now() - timedelta(days=profile.freshness_days))


def _apply_judgement(values: dict, judgement: gemini.Judgement) -> None:
    label = judgement.label
    if label == Lead.Label.LIKELY and judgement.score < settings.GEMINI_MIN_RELEVANCE:
        label = Lead.Label.REVIEW
    values.update(
        label=label,
        lead_type=judgement.lead_type,
        relevance_score=judgement.score,
        relevance_reason=judgement.reason,
        intent=judgement.intent,
        priority=Lead.priority_for(judgement.score),
        ai_summary=judgement.summary,
        suburb=judgement.suburb or values["suburb"],
        state=judgement.state or values["state"],
        postcode=judgement.postcode or values["postcode"],
    )
    values["location_unknown"] = not any((values["suburb"], values["state"], values["postcode"]))


def coverage_for(values: dict, profile: SearchProfile) -> str:
    """Inside if the suburb is a listed location or the state is served; outside if another state."""
    service_states = {state.strip().upper() for state in profile.states.split(",") if state.strip()}
    locations = {location.lower() for location in profile.location_terms()}
    if values.get("suburb") and values["suburb"].lower() in locations:
        return Lead.Coverage.INSIDE
    if values.get("state"):
        return Lead.Coverage.INSIDE if not service_states or values["state"] in service_states else Lead.Coverage.OUTSIDE
    return Lead.Coverage.UNKNOWN


def initial_status(values: dict) -> str:
    """Hot and warm client requests skip the review queue; what the AI rejected is kept for audit."""
    priority = values.get("priority")
    if not priority:
        return Lead.Status.NEW
    if values["label"] == Lead.Label.LIKELY and priority in (Lead.Priority.HOT, Lead.Priority.WARM):
        return Lead.Status.AI_QUALIFIED
    if priority == Lead.Priority.IRRELEVANT or values["label"] in (Lead.Label.JOB, Lead.Label.PROVIDER_AD):
        return Lead.Status.DISQUALIFIED
    return Lead.Status.NEW


def _downgrade_if_unproven(values: dict, record: ScrapedRecord, profile: SearchProfile, source: Source | None = None) -> None:
    """A lead is only "likely" if we can show that it is recent and in the service area.

    Web search results carry no post date. A source can opt in with "allow_undated" so
    its matches stay "likely", flagged for staff to check the date before contacting.
    """
    if values["label"] != Lead.Label.LIKELY:
        return
    if coverage_for(values, profile) == Lead.Coverage.OUTSIDE:
        values.update(label=Lead.Label.REVIEW, relevance_reason=values.get("relevance_reason") or "Outside the service area")
    elif not record.published_at and source is not None and (source.selectors or {}).get("allow_undated"):
        values["relevance_reason"] = values.get("relevance_reason") or "Post date unknown: check it is recent before contacting"
    elif not record.published_at:
        values.update(label=Lead.Label.REVIEW, relevance_reason=values.get("relevance_reason") or "Post date unknown")


def optionally_enrich_with_ai(organisation: Organisation, record: ScrapedRecord, classification: dict) -> dict:
    try:
        config: AIConfiguration = organisation.ai_configuration
    except AIConfiguration.DoesNotExist:
        return classification
    if not config.enabled or not config.base_url or not config.model:
        return classification
    api_key = config.get_api_key()
    if not api_key:
        return classification
    prompt = {
        "task": "Classify a public Australian disability/home-care service request. Do not infer diagnosis or sensitive data.",
        "allowed_labels": ["likely", "review", "job", "provider_ad", "location_unknown"],
        "record": {"title": record.title, "snippet": record.snippet},
        "current": classification,
        "output": "Return JSON only with label, lead_type, service_category_name, suburb, state, postcode, and reason.",
    }
    try:
        response = requests.post(
            config.base_url.rstrip("/") + "/chat/completions",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={
                "model": config.model,
                "temperature": 0,
                "messages": [{"role": "user", "content": json.dumps(prompt)}],
                "response_format": {"type": "json_object"},
            },
            timeout=25,
        )
        response.raise_for_status()
        content = response.json()["choices"][0]["message"]["content"]
        result = json.loads(content)
        allowed = {"likely", "review", "job", "provider_ad", "location_unknown"}
        if result.get("label") in allowed:
            classification.update({k: v for k, v in result.items() if k in classification})
    except Exception:
        return classification
    return classification


def ai_scorer():
    """The AI that qualifies leads, or None. Groq goes first: Gemini may be set up only for the AI search page."""
    if not settings.AI_LEAD_SCORING:
        return None
    return next((provider for provider in (groq, gemini) if provider.is_configured()), None)


def qualify_record(source: Source, profile: SearchProfile, record: ScrapedRecord) -> tuple[dict, ServiceCategory | None]:
    """Keyword rules first, then the AI's reading of the post when one is configured.

    Returns the lead field values (label, intent, score, priority, summary, location,
    coverage and starting status) and the service category.
    """
    classified = classify_record(record.title, record.snippet, profile, record.published_at)
    values = {
        "label": classified.label,
        "lead_type": classified.lead_type,
        "state": classified.state,
        "postcode": classified.postcode,
        "suburb": classified.suburb,
        "location_unknown": classified.location_unknown,
    }
    category = classified.category
    scorer = ai_scorer()
    if scorer:
        judgement = scorer.judge_record(record, profile)
        if judgement:
            _apply_judgement(values, judgement)
            category = judgement.category or category
    elif settings.AI_LEAD_SCORING:
        values = optionally_enrich_with_ai(profile.organisation, record, values)
    _downgrade_if_unproven(values, record, profile, source)
    values["coverage"] = coverage_for(values, profile)
    values["status"] = initial_status(values)
    return values, category


def ingest_record(source: Source, profile: SearchProfile, record: ScrapedRecord) -> tuple[Lead, bool, bool]:
    organisation = profile.organisation
    # Checked first so that a post seen on every run is not sent to the AI every time.
    existing = Lead.objects.filter(source=source, source_url=record.url).first()
    if existing:
        existing.last_checked_at = timezone.now()
        existing.source_available = True
        existing.save(update_fields=["last_checked_at", "source_available", "updated_at"])
        return existing, False, bool(existing.duplicate_of_id)
    values, category = qualify_record(source, profile, record)
    duplicate = find_possible_duplicate(record.title, record.snippet, record.public_phone, record.public_email)
    content_hash = Lead.make_hash(record.title, record.snippet, record.public_phone, record.public_email)
    defaults = {
        "organisation": organisation,
        "external_id": record.external_id[:255],
        "title": record.title[:500],
        "snippet": record.snippet,
        "public_name": record.public_name[:200],
        "public_phone": record.public_phone[:80],
        "public_email": record.public_email if "@" in record.public_email else "",
        "service_category": category,
        "published_at": record.published_at,
        "last_checked_at": timezone.now(),
        "content_hash": content_hash,
        "duplicate_of": duplicate,
        **values,
    }
    if duplicate:
        defaults["label"] = Lead.Label.DUPLICATE
    try:
        with transaction.atomic():
            lead, created = Lead.objects.get_or_create(source=source, source_url=record.url, defaults=defaults)
    except IntegrityError:
        lead = Lead.objects.get(source=source, source_url=record.url)
        created = False
    if not created:
        lead.last_checked_at = timezone.now()
        lead.source_available = True
        lead.save(update_fields=["last_checked_at", "source_available", "updated_at"])
    elif lead.label == Lead.Label.LIKELY:
        for membership in organisation.memberships.filter(active=True).select_related("user"):
            Notification.objects.create(
                user=membership.user,
                title="New likely service request",
                message=lead.title,
                url=reverse("lead-detail", args=[lead.pk]),
            )
    return lead, created, bool(duplicate)


def _ingest_fresh_records(source: Source, profile: SearchProfile, run: ScrapeRun, records: list[ScrapedRecord]) -> None:
    run.pages_fetched += 1
    run.records_seen += len(records)
    for record in records:
        if is_stale(record, profile):
            continue
        _, created, duplicate = ingest_record(source, profile, record)
        run.leads_created += int(created)
        run.duplicates_found += int(duplicate)
    run.save(update_fields=["pages_fetched", "records_seen", "leads_created", "duplicates_found", "updated_at"])


def _run_api_source(source: Source, profile: SearchProfile, run: ScrapeRun):
    for _, records in fetch_api_source(source, profile):
        _ingest_fresh_records(source, profile, run, records)
        polite_pause(source)


def _run_selenium_search(source: Source, profile: SearchProfile, run: ScrapeRun):
    """Run the source's own search queries in one browser.

    Only results with both request language ("looking for", "need", "recommend") and a
    service keyword are kept, as for the API sources.
    """
    keywords = service_keywords(profile)
    driver = open_browser()
    try:
        for query in api_requests(source, profile):
            records = scrape_source(source, build_search_url(source, query, "", 1), driver)
            _ingest_fresh_records(source, profile, run, [record for record in records if is_service_request(record, keywords)])
            polite_pause(source)
    finally:
        driver.quit()


def run_source_profile(source: Source, profile: SearchProfile) -> ScrapeRun:
    run = ScrapeRun.objects.create(source=source, profile=profile)
    try:
        selenium_search = source.source_type == Source.SourceType.SELENIUM and (source.selectors or {}).get("queries")
        if source.source_type in API_SOURCE_TYPES or selenium_search:
            if selenium_search:
                _run_selenium_search(source, profile, run)
            else:
                _run_api_source(source, profile, run)
            run.status = ScrapeRun.Status.SUCCESS
            source.last_error = ""
            return run
        keywords = profile.keywords.filter(kind="positive", enabled=True)
        locations = profile.location_terms() or [""]
        for keyword in keywords:
            for location in locations:
                for page in range(1, source.max_pages + 1):
                    url = build_search_url(source, keyword.term, location, page)
                    _ingest_fresh_records(source, profile, run, scrape_source(source, url))
                    polite_pause(source)
        run.status = ScrapeRun.Status.SUCCESS
        source.last_error = ""
    except Exception as exc:
        run.status = ScrapeRun.Status.FAILED
        run.error = str(exc)
        source.last_error = str(exc)
    finally:
        run.finished_at = timezone.now()
        source.last_checked_at = timezone.now()
        source.save(update_fields=["last_checked_at", "last_error", "updated_at"])
        run.save(update_fields=["status", "error", "finished_at", "updated_at"])
    return run


def archive_stale_leads(days: int = 90) -> int:
    cutoff = timezone.now() - timedelta(days=days)
    return Lead.objects.filter(discovered_at__lt=cutoff, archived_at__isnull=True).exclude(
        status__in=[Lead.Status.CONVERTED, Lead.Status.DO_NOT_CONTACT]
    ).update(status=Lead.Status.ARCHIVED, archived_at=timezone.now())


class ScanAlreadyRunning(Exception):
    pass


LOCK_MAX_AGE_SECONDS = 60 * 60 * 3


def scraper_lock_path():
    return Path(settings.SCRAPER_LOCK_FILE)


def _process_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        import ctypes

        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        code = ctypes.c_ulong()
        kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
        kernel32.CloseHandle(handle)
        return code.value == 259  # STILL_ACTIVE
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def scan_is_running() -> bool:
    """True while a scan holds the lock. A lock left by a crashed scan is ignored."""
    path = scraper_lock_path()
    try:
        if timezone.now().timestamp() - path.stat().st_mtime >= LOCK_MAX_AGE_SECONDS:
            return False
        content = path.read_text(encoding="utf-8").strip()
    except OSError:
        return False
    pid = content.split()[0] if content else ""
    # Locks from before the process ID was recorded hold a timestamp; trust their age.
    return _process_alive(int(pid)) if pid.isdigit() else True


def run_all_sources(source_id: int | None = None, profile_id: int | None = None, log=lambda message: None) -> dict:
    """Runs approved sources one at a time. The lock file keeps SQLite to a single writer, whether started by cron, Celery or the website."""
    path = scraper_lock_path()
    if scan_is_running():
        raise ScanAlreadyRunning("A scan is already running. It clears by itself after three hours if it was interrupted.")
    path.unlink(missing_ok=True)
    path.write_text(f"{os.getpid()} {timezone.now().isoformat()}", encoding="utf-8")
    # Runs still marked running belong to a scan that died, since no other scan holds the lock.
    ScrapeRun.objects.filter(status=ScrapeRun.Status.RUNNING).update(
        status=ScrapeRun.Status.FAILED, error="Interrupted before it finished.", finished_at=timezone.now()
    )
    totals = {"runs": 0, "failed": 0, "leads_created": 0, "duplicates_found": 0, "archived": 0}
    try:
        sources = Source.objects.filter(enabled=True, review_status=Source.ReviewStatus.APPROVED, robots_reviewed=True, terms_reviewed=True)
        profiles = SearchProfile.objects.filter(active=True).prefetch_related("keywords")
        if source_id:
            sources = sources.filter(pk=source_id)
        if profile_id:
            profiles = profiles.filter(pk=profile_id)
        for source in sources:
            for profile in profiles:
                log(f"Running {source} / {profile}")
                run = run_source_profile(source, profile)
                totals["runs"] += 1
                totals["failed"] += int(run.status == ScrapeRun.Status.FAILED)
                totals["leads_created"] += run.leads_created
                totals["duplicates_found"] += run.duplicates_found
                log(f"{run.status}: {run.leads_created} new lead(s), {run.duplicates_found} duplicate(s)")
        totals["archived"] = archive_stale_leads()
        log(f"Archived {totals['archived']} stale lead(s).")
    finally:
        path.unlink(missing_ok=True)
    return totals
