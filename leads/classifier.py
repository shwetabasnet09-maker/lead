from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from difflib import SequenceMatcher

from django.utils import timezone

from .models import Keyword, Lead, SearchProfile, ServiceCategory


JOB_TERMS = {
    "job", "jobs", "hiring", "vacancy", "vacancies", "career", "careers", "apply now",
    "resume", "cv", "salary", "employment", "position available", "join our team",
}
PROVIDER_AD_TERMS = {
    "we provide", "our services", "registered provider", "contact us today", "we offer",
    "now accepting participants", "provider near you", "book a consultation",
}
GENERAL_EXCLUSIONS = {
    "course", "training", "webinar", "conference", "news", "guide", "policy update",
}
INTENT_TERMS = {
    "looking for", "need", "seeking", "recommend", "recommendation", "available provider",
    "help me find", "does anyone know", "urgent", "wanted", "can anyone help",
}
LEAD_TYPE_TERMS = {
    Lead.LeadType.COORDINATOR: {"support coordinator", "coordinator seeking", "for my participant"},
    Lead.LeadType.FAMILY: {"my son", "my daughter", "my child", "my mum", "my dad", "family member", "carer"},
    Lead.LeadType.ORGANISATION: {"discharge", "hospital", "community organisation", "case manager"},
    Lead.LeadType.PARTICIPANT: {"i need", "i am looking", "for myself", "my ndis"},
}
STATE_NAMES = {
    "victoria": "VIC", "new south wales": "NSW", "queensland": "QLD", "western australia": "WA",
    "south australia": "SA", "tasmania": "TAS", "australian capital territory": "ACT",
    "northern territory": "NT",
}
STATE_CODES = tuple(STATE_NAMES.values())


@dataclass
class Classification:
    label: str
    lead_type: str
    category: ServiceCategory | None
    state: str
    postcode: str
    suburb: str
    location_unknown: bool
    reasons: list[str]


def _contains(text: str, terms) -> bool:
    return any(term.lower() in text for term in terms)


def _contains_word(text: str, terms) -> bool:
    # Look around the complete phrase instead of using ``\b``. A word boundary
    # does not match after punctuation, so labels such as "Accommodation (STA)"
    # were previously missed when used as exact search terms.
    lowered = text.lower()
    return any(re.search(rf"(?<!\w){re.escape(term.lower())}(?!\w)", lowered) for term in terms)


def classify_record(
    title: str,
    snippet: str,
    profile: SearchProfile,
    published_at: datetime | None = None,
) -> Classification:
    text = f"{title} {snippet}".lower()
    reasons: list[str] = []
    positive = list(profile.keywords.filter(kind=Keyword.Kind.POSITIVE, enabled=True).select_related("category"))
    negative = [k.term.lower() for k in profile.keywords.filter(kind=Keyword.Kind.NEGATIVE, enabled=True)]

    category = next((k.category for k in positive if k.category and _contains_word(text, [k.term])), None)
    if category is None:
        for candidate in ServiceCategory.objects.filter(active=True):
            if _contains_word(text, candidate.terms()):
                category = candidate
                break

    postcode_match = re.search(r"\b([0-9]{4})\b", text)
    postcode = postcode_match.group(1) if postcode_match else ""
    state = ""
    for name, code in STATE_NAMES.items():
        if name in text or re.search(rf"\b{code.lower()}\b", text):
            state = code
            break

    suburb = next((loc for loc in profile.location_terms() if loc.lower() in text and not loc.isdigit()), "")
    location_unknown = not any((postcode, state, suburb))

    if negative and _contains(text, negative):
        return Classification(Lead.Label.REVIEW, Lead.LeadType.UNKNOWN, category, state, postcode, suburb, location_unknown, ["Negative keyword matched"])
    if _contains(text, JOB_TERMS):
        return Classification(Lead.Label.JOB, Lead.LeadType.UNKNOWN, category, state, postcode, suburb, location_unknown, ["Employment language detected"])
    if _contains(text, PROVIDER_AD_TERMS):
        return Classification(Lead.Label.PROVIDER_AD, Lead.LeadType.UNKNOWN, category, state, postcode, suburb, location_unknown, ["Provider advertising language detected"])
    if _contains(text, GENERAL_EXCLUSIONS):
        return Classification(Lead.Label.REVIEW, Lead.LeadType.UNKNOWN, category, state, postcode, suburb, location_unknown, ["Informational/promotional content detected"])

    lead_type = Lead.LeadType.UNKNOWN
    for candidate, terms in LEAD_TYPE_TERMS.items():
        if _contains(text, terms):
            lead_type = candidate
            break

    keyword_match = any(k.term.lower() in text for k in positive)
    intent_match = _contains(text, INTENT_TERMS)
    if keyword_match:
        reasons.append("Keyword matched")
    if intent_match:
        reasons.append("Service-seeking language detected")
    if location_unknown:
        return Classification(Lead.Label.LOCATION_UNKNOWN, lead_type, category, state, postcode, suburb, True, reasons or ["Location not found"])
    if published_at and published_at < timezone.now() - timedelta(days=profile.freshness_days):
        return Classification(Lead.Label.REVIEW, lead_type, category, state, postcode, suburb, False, ["Outside freshness window"])
    label = Lead.Label.LIKELY if keyword_match and intent_match else Lead.Label.REVIEW
    return Classification(label, lead_type, category, state, postcode, suburb, False, reasons)


def find_possible_duplicate(title: str, snippet: str, phone: str = "", email: str = "") -> Lead | None:
    if phone:
        match = Lead.objects.filter(public_phone=phone).exclude(public_phone="").first()
        if match:
            return match
    if email:
        match = Lead.objects.filter(public_email__iexact=email).exclude(public_email="").first()
        if match:
            return match
    candidates = Lead.objects.order_by("-discovered_at")[:250]
    needle = f"{title} {snippet}"[:1500].lower()
    for candidate in candidates:
        other = f"{candidate.title} {candidate.snippet}"[:1500].lower()
        if SequenceMatcher(None, needle, other).ratio() >= 0.92:
            return candidate
    return None
