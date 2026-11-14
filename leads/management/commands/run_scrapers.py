from django.core.management.base import BaseCommand, CommandError

from leads.services import ScanAlreadyRunning, run_all_sources


class Command(BaseCommand):
    help = "Run approved sources sequentially. Used by cron, Celery and the Scan now button."

    def add_arguments(self, parser):
        parser.add_argument("--source", type=int, help="Run one source ID")
        parser.add_argument("--profile", type=int, help="Run one search profile ID")

    def handle(self, *args, **options):
        try:
            run_all_sources(options["source"], options["profile"], log=self.stdout.write)
        except ScanAlreadyRunning as exc:
            raise CommandError(str(exc)) from exc
