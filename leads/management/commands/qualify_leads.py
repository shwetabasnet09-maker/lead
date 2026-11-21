from django.core.management.base import BaseCommand, CommandError

from leads.models import Lead
from leads.scrapers import ScrapedRecord
from leads.services import active_profile, ai_scorer, qualify_record

# Statuses a person has not touched yet. Anything else is staff's decision and is kept.
UNTOUCHED = {Lead.Status.NEW, Lead.Status.REVIEW, Lead.Status.AI_QUALIFIED, Lead.Status.DISQUALIFIED}


class Command(BaseCommand):
    help = "Run AI qualification on leads that were saved before it was set up, or again on all leads."

    def add_arguments(self, parser):
        parser.add_argument("--all", action="store_true", help="Also re-qualify leads that already have an AI score.")
        parser.add_argument("--limit", type=int, default=50, help="Most leads to send to the AI in one run (default 50).")

    def handle(self, *args, **options):
        if ai_scorer() is None:
            raise CommandError("No AI is configured. Set GROQ_API_KEY or GEMINI_API_KEY, and leave AI_LEAD_SCORING on.")
        leads = Lead.objects.select_related("source", "organisation").order_by("-discovered_at")
        if not options["all"]:
            leads = leads.filter(relevance_score__isnull=True)
        done = 0
        for lead in leads[: options["limit"]]:
            profile = active_profile(lead.organisation)
            if profile is None:
                continue
            record = ScrapedRecord(
                title=lead.title,
                url=lead.source_url,
                snippet=lead.snippet,
                published_at=lead.published_at,
                public_name=lead.public_name,
                public_phone=lead.public_phone,
                public_email=lead.public_email,
            )
            values, category = qualify_record(lead.source, profile, record)
            if values.get("relevance_score") is None:
                self.stdout.write(self.style.WARNING(f"Lead {lead.pk}: the AI did not answer, left unchanged."))
                continue
            if lead.status not in UNTOUCHED:
                values.pop("status")
            if lead.duplicate_of_id:
                values.pop("label")
            for field, value in values.items():
                setattr(lead, field, value)
            lead.service_category = category or lead.service_category
            lead.save()
            done += 1
            self.stdout.write(f"Lead {lead.pk}: {lead.get_priority_display()}, {lead.get_intent_display()}, {lead.get_status_display()}")
        self.stdout.write(self.style.SUCCESS(f"Qualified {done} lead(s)."))
