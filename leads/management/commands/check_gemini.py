from django.core.management.base import BaseCommand, CommandError

from leads import gemini
from leads.models import SearchProfile
from leads.scrapers import ScrapedRecord


class Command(BaseCommand):
    help = "Make one real call for each Gemini feature to confirm the key, the model and the response format."

    def handle(self, *args, **options):
        if not gemini.is_configured():
            raise CommandError("GEMINI_API_KEY is not set.")
        profile = SearchProfile.objects.filter(active=True).order_by("id").first()
        if profile is None:
            raise CommandError("No active search profile. Run seed_initial_data first.")

        sample = ScrapedRecord(
            title="Looking for a support worker in Dandenong",
            url="https://example.com/fictional-test-post",
            snippet="Fictional test post. I need a support worker three mornings a week in Dandenong VIC 3175.",
        )
        judgement = gemini.judge_record(sample, profile)
        if judgement is None:
            raise CommandError("Scoring failed. The warning above gives the reason.")
        self.stdout.write(self.style.SUCCESS(f"Scoring works: {judgement.label}, {judgement.score}/100. {judgement.reason}"))

        try:
            result = gemini.search_for_leads("Support worker", "Melbourne, VIC", profile.freshness_days)
        except gemini.GeminiError as exc:
            raise CommandError(f"Live search failed: {exc}") from exc
        self.stdout.write(self.style.SUCCESS(f"Live search works: {len(result.citations)} source(s), search suggestions {'present' if result.suggestions_html else 'MISSING'}."))
