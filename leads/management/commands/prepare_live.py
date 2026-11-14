from django.contrib.auth.models import User
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError

from leads.models import Source


class Command(BaseCommand):
    help = "Get the database ready for the live site: remove demo data, create the defaults and approve named sources. Safe to run on every deploy."

    def add_arguments(self, parser):
        parser.add_argument(
            "--approve-source",
            action="append",
            default=[],
            metavar="NAME",
            help="Enable a source whose terms you have reviewed. Repeat for more than one.",
        )

    def handle(self, *args, **options):
        call_command("seed_demo_leads", clear=True, stdout=self.stdout)

        owner = User.objects.filter(is_superuser=True).order_by("id").first()
        seed_options = {"username": owner.username} if owner else {}
        call_command("seed_initial_data", stdout=self.stdout, **seed_options)

        for name in options["approve_source"]:
            source = Source.objects.filter(name=name).first()
            if source is None:
                raise CommandError(f"No source is named {name!r}.")
            source.review_status = Source.ReviewStatus.APPROVED
            source.robots_reviewed = True
            source.terms_reviewed = True
            source.enabled = True
            source.save(update_fields=["review_status", "robots_reviewed", "terms_reviewed", "enabled", "updated_at"])

        running = [source.name for source in Source.objects.filter(enabled=True) if source.is_runnable]
        if running:
            self.stdout.write(self.style.SUCCESS(f"Sources that will be scraped automatically: {', '.join(running)}"))
        else:
            self.stdout.write(self.style.WARNING("No sources are approved, so automatic scraping will find nothing."))
