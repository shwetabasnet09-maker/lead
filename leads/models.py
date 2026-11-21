from __future__ import annotations

import hashlib
from datetime import timedelta

from django.conf import settings
from django.contrib.auth.models import User
from django.core.validators import MaxValueValidator, MinValueValidator, RegexValidator
from django.db import models
from django.utils import timezone


class TimeStampedModel(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class Organisation(TimeStampedModel):
    name = models.CharField(max_length=160, default="NDIS Lead Finder")
    slug = models.SlugField(unique=True, default="default")
    logo = models.ImageField(upload_to="organisation/", blank=True)
    primary_colour = models.CharField(max_length=7, default="#0e7490")
    active = models.BooleanField(default=True)

    def __str__(self):
        return self.name


class Membership(TimeStampedModel):
    class Role(models.TextChoices):
        OWNER = "owner", "Business owner"
        MANAGER = "manager", "Manager"
        EMPLOYEE = "employee", "Employee"
        READ_ONLY = "read_only", "Read only"

    organisation = models.ForeignKey(Organisation, on_delete=models.CASCADE, related_name="memberships")
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="organisation_memberships")
    role = models.CharField(max_length=20, choices=Role.choices, default=Role.EMPLOYEE)
    active = models.BooleanField(default=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["organisation", "user"], name="unique_org_member")]

    def __str__(self):
        return f"{self.user} — {self.get_role_display()}"


class ServiceCategory(TimeStampedModel):
    name = models.CharField(max_length=120, unique=True)
    slug = models.SlugField(unique=True)
    synonyms = models.TextField(blank=True, help_text="One synonym per line")
    active = models.BooleanField(default=True)

    def terms(self):
        return [self.name, *[x.strip() for x in self.synonyms.splitlines() if x.strip()]]

    def __str__(self):
        return self.name


class SearchProfile(TimeStampedModel):
    organisation = models.ForeignKey(Organisation, on_delete=models.CASCADE, related_name="search_profiles")
    name = models.CharField(max_length=120, default="Primary search")
    states = models.CharField(max_length=80, default="VIC", help_text="Comma-separated state codes")
    locations = models.TextField(blank=True, help_text="One suburb or postcode per line")
    radius_km = models.PositiveIntegerField(default=50, validators=[MaxValueValidator(1000)])
    daily_lead_limit = models.PositiveSmallIntegerField(default=6, validators=[MinValueValidator(1), MaxValueValidator(100)])
    scan_interval_hours = models.PositiveSmallIntegerField(default=6, validators=[MinValueValidator(1), MaxValueValidator(168)])
    freshness_days = models.PositiveSmallIntegerField(
        default=7,
        validators=[MinValueValidator(1), MaxValueValidator(365)],
        help_text="Posts older than this many days are not added as leads.",
    )
    active = models.BooleanField(default=True)

    def location_terms(self):
        return [x.strip() for x in self.locations.splitlines() if x.strip()]

    def __str__(self):
        return self.name


class Keyword(TimeStampedModel):
    class Kind(models.TextChoices):
        POSITIVE = "positive", "Positive"
        NEGATIVE = "negative", "Negative"

    profile = models.ForeignKey(SearchProfile, on_delete=models.CASCADE, related_name="keywords")
    term = models.CharField(max_length=180)
    kind = models.CharField(max_length=10, choices=Kind.choices, default=Kind.POSITIVE)
    category = models.ForeignKey(ServiceCategory, on_delete=models.SET_NULL, null=True, blank=True)
    generated = models.BooleanField(default=False)
    enabled = models.BooleanField(default=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["profile", "term", "kind"], name="unique_profile_keyword")]
        ordering = ["kind", "term"]

    def __str__(self):
        return self.term


class Source(TimeStampedModel):
    class SourceType(models.TextChoices):
        RSS = "rss", "RSS / Atom"
        HTML = "html", "Generic HTML"
        SELENIUM = "selenium", "Selenium"
        MASTODON = "mastodon", "Mastodon API (hashtags)"
        BLUESKY = "bluesky", "Bluesky API (search)"
        BRAVE = "brave", "Brave Search API"

    class ReviewStatus(models.TextChoices):
        PENDING = "pending", "Pending review"
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"

    name = models.CharField(max_length=140)
    source_type = models.CharField(max_length=20, choices=SourceType.choices)
    base_url = models.URLField()
    search_url_template = models.CharField(
        max_length=600,
        help_text="Supports {query}, {location}, and {page}. The domain must match Base URL.",
    )
    selectors = models.JSONField(default=dict, blank=True, help_text="CSS selectors for item, title, link, snippet, date, name, phone and email")
    review_status = models.CharField(max_length=20, choices=ReviewStatus.choices, default=ReviewStatus.PENDING)
    robots_reviewed = models.BooleanField(default=False)
    terms_reviewed = models.BooleanField(default=False)
    enabled = models.BooleanField(default=False)
    request_delay_seconds = models.PositiveSmallIntegerField(default=3, validators=[MinValueValidator(1), MaxValueValidator(120)])
    max_pages = models.PositiveSmallIntegerField(default=1, validators=[MinValueValidator(1), MaxValueValidator(20)])
    last_checked_at = models.DateTimeField(null=True, blank=True)
    last_error = models.TextField(blank=True)

    @property
    def is_runnable(self):
        return self.enabled and self.review_status == self.ReviewStatus.APPROVED and self.robots_reviewed and self.terms_reviewed

    def __str__(self):
        return self.name


class ScrapeRun(TimeStampedModel):
    class Status(models.TextChoices):
        RUNNING = "running", "Running"
        SUCCESS = "success", "Success"
        PARTIAL = "partial", "Partial"
        FAILED = "failed", "Failed"
        SKIPPED = "skipped", "Skipped"

    source = models.ForeignKey(Source, on_delete=models.CASCADE, related_name="runs")
    profile = models.ForeignKey(SearchProfile, on_delete=models.CASCADE, related_name="runs")
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.RUNNING)
    started_at = models.DateTimeField(default=timezone.now)
    finished_at = models.DateTimeField(null=True, blank=True)
    pages_fetched = models.PositiveIntegerField(default=0)
    records_seen = models.PositiveIntegerField(default=0)
    leads_created = models.PositiveIntegerField(default=0)
    duplicates_found = models.PositiveIntegerField(default=0)
    error = models.TextField(blank=True)

    def __str__(self):
        return f"{self.source} — {self.started_at:%Y-%m-%d %H:%M}"


class Lead(TimeStampedModel):
    class LeadType(models.TextChoices):
        PARTICIPANT = "participant", "Participant"
        FAMILY = "family", "Family / carer"
        COORDINATOR = "coordinator", "Support coordinator"
        ORGANISATION = "organisation", "Organisation"
        UNKNOWN = "unknown", "Unknown"

    class Label(models.TextChoices):
        NEW = "new", "New"
        REVIEW = "review", "Needs review"
        LIKELY = "likely", "Likely service request"
        JOB = "job", "Possible job post"
        PROVIDER_AD = "provider_ad", "Possible provider advertisement"
        DUPLICATE = "duplicate", "Possible duplicate"
        LOCATION_UNKNOWN = "location_unknown", "Location unknown"

    class Intent(models.TextChoices):
        SEEKING_SERVICES = "seeking_services", "Seeking NDIS services"
        FAMILY = "family", "Family member seeking for someone"
        COORDINATOR = "coordinator", "Support coordinator seeking a provider"
        WORKERS_FOR_CLIENT = "workers_for_client", "Seeking support workers for a client"
        CHANGING_PROVIDER = "changing_provider", "Participant changing provider"
        BOOKING = "booking", "Wants to book a consultation"
        JOB_SEEKER = "job_seeker", "Job seeker"
        PROVIDER_AD = "provider_ad", "Provider advertising"
        INFORMATIONAL = "informational", "General discussion or news"
        UNKNOWN = "unknown", "Not assessed"

    class Priority(models.TextChoices):
        HOT = "hot", "Hot (80-100)"
        WARM = "warm", "Warm (50-79)"
        COLD = "cold", "Cold (20-49)"
        IRRELEVANT = "irrelevant", "Irrelevant (under 20)"

    class Coverage(models.TextChoices):
        INSIDE = "inside", "Inside coverage"
        OUTSIDE = "outside", "Outside current coverage"
        UNKNOWN = "unknown", "Location unknown"

    class Status(models.TextChoices):
        NEW = "new", "New"
        AI_QUALIFIED = "ai_qualified", "AI qualified"
        REVIEW = "review", "Needs review"
        QUALIFIED = "qualified", "Qualified"
        CONTACT_ATTEMPTED = "contact_attempted", "Contact attempted"
        CONTACTED = "contacted", "Contacted"
        FOLLOW_UP = "follow_up", "Follow-up scheduled"
        INTERESTED = "interested", "Interested"
        CONSULTATION_BOOKED = "consultation_booked", "Consultation booked"
        QUOTED = "quoted", "Quotation sent"
        CONVERTED = "converted", "Converted"
        NOT_INTERESTED = "not_interested", "Not interested"
        DISQUALIFIED = "disqualified", "Disqualified"
        INVALID = "invalid", "Invalid / spam"
        DO_NOT_CONTACT = "do_not_contact", "Do not contact"
        CLOSED = "closed", "Closed"
        ARCHIVED = "archived", "Archived"

    organisation = models.ForeignKey(Organisation, on_delete=models.CASCADE, related_name="leads")
    source = models.ForeignKey(Source, on_delete=models.PROTECT, related_name="leads")
    service_category = models.ForeignKey(ServiceCategory, on_delete=models.SET_NULL, null=True, blank=True)
    source_url = models.URLField(max_length=1000)
    external_id = models.CharField(max_length=255, blank=True)
    title = models.CharField(max_length=500)
    snippet = models.TextField(blank=True)
    public_name = models.CharField(max_length=200, blank=True)
    public_phone = models.CharField(max_length=80, blank=True)
    public_email = models.EmailField(blank=True)
    lead_type = models.CharField(max_length=20, choices=LeadType.choices, default=LeadType.UNKNOWN)
    suburb = models.CharField(max_length=120, blank=True)
    state = models.CharField(max_length=3, blank=True)
    postcode = models.CharField(max_length=4, blank=True, validators=[RegexValidator(r"^\d{4}$", "Use a four-digit Australian postcode")])
    location_unknown = models.BooleanField(default=False)
    published_at = models.DateTimeField(null=True, blank=True)
    discovered_at = models.DateTimeField(default=timezone.now)
    last_checked_at = models.DateTimeField(default=timezone.now)
    source_available = models.BooleanField(default=True)
    label = models.CharField(max_length=30, choices=Label.choices, default=Label.REVIEW)
    relevance_score = models.PositiveSmallIntegerField(null=True, blank=True, help_text="The AI's 0-100 score. Empty if the lead was not scored by AI.")
    relevance_reason = models.CharField(max_length=255, blank=True)
    intent = models.CharField(max_length=30, choices=Intent.choices, default=Intent.UNKNOWN)
    priority = models.CharField(max_length=12, choices=Priority.choices, blank=True, help_text="Set from the AI score. Empty if not scored.")
    coverage = models.CharField(max_length=10, choices=Coverage.choices, default=Coverage.UNKNOWN)
    ai_summary = models.CharField(max_length=300, blank=True)
    status = models.CharField(max_length=30, choices=Status.choices, default=Status.NEW)
    content_hash = models.CharField(max_length=64, db_index=True)
    duplicate_of = models.ForeignKey("self", on_delete=models.SET_NULL, null=True, blank=True, related_name="possible_duplicates")
    claimed_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name="claimed_leads")
    claimed_at = models.DateTimeField(null=True, blank=True)
    follow_up_at = models.DateTimeField(null=True, blank=True)
    archived_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-published_at", "-discovered_at"]
        constraints = [models.UniqueConstraint(fields=["source", "source_url"], name="unique_source_lead_url")]
        indexes = [
            models.Index(fields=["organisation", "status"]),
            models.Index(fields=["organisation", "label"]),
            models.Index(fields=["published_at"]),
            models.Index(fields=["organisation", "priority"]),
        ]

    # People who could become clients. Every other intent is not a lead.
    CLIENT_INTENTS = frozenset(
        {
            Intent.SEEKING_SERVICES,
            Intent.FAMILY,
            Intent.COORDINATOR,
            Intent.WORKERS_FOR_CLIENT,
            Intent.CHANGING_PROVIDER,
            Intent.BOOKING,
        }
    )

    @classmethod
    def priority_for(cls, score: int | None) -> str:
        if score is None:
            return ""
        if score >= 80:
            return cls.Priority.HOT
        if score >= 50:
            return cls.Priority.WARM
        if score >= 20:
            return cls.Priority.COLD
        return cls.Priority.IRRELEVANT

    @staticmethod
    def make_hash(title: str, snippet: str, phone: str = "", email: str = "") -> str:
        value = "|".join(x.strip().lower() for x in (title, snippet, phone, email))
        return hashlib.sha256(value.encode("utf-8", errors="ignore")).hexdigest()

    def default_archive_date(self):
        return self.discovered_at + timedelta(days=90)

    def __str__(self):
        return self.title


class LeadActivity(TimeStampedModel):
    lead = models.ForeignKey(Lead, on_delete=models.CASCADE, related_name="activities")
    user = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name="lead_activities")
    action = models.CharField(max_length=80)
    note = models.TextField(blank=True)
    from_status = models.CharField(max_length=30, blank=True)
    to_status = models.CharField(max_length=30, blank=True)
    follow_up_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.lead_id}: {self.action}"


class Notification(TimeStampedModel):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="lead_notifications")
    title = models.CharField(max_length=180)
    message = models.TextField(blank=True)
    url = models.CharField(max_length=500, blank=True)
    read_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]


class AuditLog(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    user = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)
    action = models.CharField(max_length=100)
    object_type = models.CharField(max_length=100)
    object_id = models.CharField(max_length=100, blank=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-created_at"]


class AIConfiguration(TimeStampedModel):
    organisation = models.OneToOneField(Organisation, on_delete=models.CASCADE, related_name="ai_configuration")
    enabled = models.BooleanField(default=False)
    base_url = models.URLField(blank=True, help_text="OpenAI-compatible API base URL")
    model = models.CharField(max_length=120, blank=True)
    api_key_ciphertext = models.TextField(blank=True, editable=False)

    def __str__(self):
        return f"AI configuration for {self.organisation}"

    def set_api_key(self, raw_key: str):
        if not raw_key:
            self.api_key_ciphertext = ""
            return
        if not settings.FIELD_ENCRYPTION_KEY:
            raise ValueError("FIELD_ENCRYPTION_KEY must be configured before saving an AI API key")
        from cryptography.fernet import Fernet

        self.api_key_ciphertext = Fernet(settings.FIELD_ENCRYPTION_KEY.encode()).encrypt(raw_key.encode()).decode()

    def get_api_key(self) -> str:
        if not self.api_key_ciphertext or not settings.FIELD_ENCRYPTION_KEY:
            return ""
        from cryptography.fernet import Fernet

        return Fernet(settings.FIELD_ENCRYPTION_KEY.encode()).decrypt(self.api_key_ciphertext.encode()).decode()


class LoginThrottle(models.Model):
    identifier_hash = models.CharField(max_length=64, unique=True)
    failures = models.PositiveSmallIntegerField(default=0)
    first_failed_at = models.DateTimeField(default=timezone.now)
    last_failed_at = models.DateTimeField(default=timezone.now)
    locked_until = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"Login throttle {self.identifier_hash[:10]}…"
