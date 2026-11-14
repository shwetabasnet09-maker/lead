from datetime import timedelta

from django.conf import settings
from django.contrib.auth.models import User
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from leads.models import Lead, Membership, Notification, SearchProfile, Source
from leads.scrapers import ScrapedRecord
from leads.services import default_organisation, ingest_record

DEMO_SOURCE_NAME = "Demo data (fictional)"

# Fictional posts. Phone numbers use the ACMA range reserved for fiction (0491 570 xxx).
DEMO_RECORDS = [
    ("Looking for home care support in Dandenong VIC", "Hi all, I need a support worker for in-home support three mornings a week. NDIS self-managed. Dandenong 3175.", "Sam R.", "0491 570 006", "", 1),
    ("Need personal care for my mum in Geelong", "Seeking a personal care provider for my mum, weekday mornings. Geelong VIC 3220. Can anyone help?", "Alex P.", "0491 570 156", "alex.p@example.com", 1),
    ("Support coordinator seeking OT for participant – Melbourne", "Support coordinator seeking an occupational therapist with availability for a participant in Melbourne VIC 3000.", "Jordan K.", "", "jordan.k@example.com", 2),
    ("Can anyone recommend NDIS cleaning in Melbourne?", "Looking for domestic assistance / ndis cleaning fortnightly, Melbourne 3121.", "", "0491 570 157", "", 2),
    ("Urgent: respite needed for my son this weekend", "My son needs short term accommodation / respite, Dandenong VIC. Urgent, please message.", "Taylor M.", "0491 570 158", "", 3),
    ("Seeking speech therapy for my daughter", "Does anyone know a speech pathologist with openings? Geelong 3216.", "Casey L.", "", "casey.l@example.com", 4),
    ("Need physiotherapy at home", "I need a physiotherapist who does home visits. My NDIS plan covers it.", "Morgan D.", "", "", 5),
    ("Hiring disability support worker – Melbourne", "We are hiring! Apply now for support worker vacancies in Melbourne VIC. Salary negotiable.", "", "", "", 1),
    ("We provide NDIS transport across Victoria", "Registered provider. We offer accessible transport. Contact us today.", "", "", "", 2),
    ("Looking for a plan manager – Dandenong", "Need help finding a plan manager for my NDIS plan, Dandenong VIC 3175.", "Riley S.", "0491 570 159", "", 6),
]


class Command(BaseCommand):
    help = "Load clearly-labelled fictional demo leads through the real classifier, so the dashboard has data to show."

    def add_arguments(self, parser):
        parser.add_argument("--clear", action="store_true", help="Delete demo leads and the demo source instead of creating them")

    def handle(self, *args, **options):
        if options["clear"]:
            demo_titles = [record[0] for record in DEMO_RECORDS]
            notifications, _ = Notification.objects.filter(title="New likely service request", message__in=demo_titles).delete()
            source = Source.objects.filter(name=DEMO_SOURCE_NAME).first()
            count = 0
            if source:
                count, _ = Lead.objects.filter(source=source).delete()
                source.delete()
            if source or notifications:
                self.stdout.write(self.style.SUCCESS(f"Removed demo data: {count} lead record(s) and {notifications} notification(s)."))
            else:
                self.stdout.write("No demo data found.")
            return

        if not settings.DEBUG:
            raise CommandError("Demo data cannot be loaded on a live site (DJANGO_DEBUG is false).")

        organisation = default_organisation()
        profile = SearchProfile.objects.filter(organisation=organisation, active=True).order_by("id").first()
        if profile is None:
            call_command("seed_initial_data")
            profile = SearchProfile.objects.filter(organisation=organisation, active=True).order_by("id").first()

        for user in User.objects.filter(is_superuser=True):
            Membership.objects.get_or_create(organisation=organisation, user=user, defaults={"role": Membership.Role.OWNER})

        # Disabled and unapproved, so run_scrapers never crawls it.
        source, _ = Source.objects.get_or_create(
            name=DEMO_SOURCE_NAME,
            defaults={"source_type": Source.SourceType.HTML, "base_url": "https://example.com/", "search_url_template": "https://example.com/", "enabled": False},
        )

        now = timezone.now()
        created_total = 0
        for index, (title, snippet, name, phone, email, days_ago) in enumerate(DEMO_RECORDS, start=1):
            record = ScrapedRecord(
                title=title,
                url=f"https://example.com/demo/{index}",
                snippet=snippet,
                published_at=now - timedelta(days=days_ago),
                public_name=name,
                public_phone=phone,
                public_email=email,
                external_id=f"demo-{index}",
            )
            lead, created, _ = ingest_record(source, profile, record)
            if created:
                lead.discovered_at = now - timedelta(days=days_ago - 1, hours=index)
                lead.save(update_fields=["discovered_at", "updated_at"])
            created_total += int(created)

        self.stdout.write(self.style.SUCCESS(f"Created {created_total} demo lead(s). Remove them with: python manage.py seed_demo_leads --clear"))
