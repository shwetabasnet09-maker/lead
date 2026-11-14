import shutil
from datetime import timedelta
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone


class Command(BaseCommand):
    help = "Create a timestamped SQLite backup and prune backups beyond retention."

    def handle(self, *args, **options):
        engine = settings.DATABASES["default"]["ENGINE"]
        if engine != "django.db.backends.sqlite3":
            raise CommandError("This command currently supports SQLite only. Use the database server's native backup tool after migration.")
        source = Path(settings.DATABASES["default"]["NAME"])
        if not source.exists():
            raise CommandError(f"Database file not found: {source}")
        settings.BACKUP_DIR.mkdir(parents=True, exist_ok=True)
        timestamp = timezone.localtime().strftime("%Y%m%d-%H%M%S")
        destination = settings.BACKUP_DIR / f"db-{timestamp}.sqlite3"
        shutil.copy2(source, destination)
        cutoff = timezone.now() - timedelta(days=settings.BACKUP_RETENTION_DAYS)
        removed = 0
        for candidate in settings.BACKUP_DIR.glob("db-*.sqlite3"):
            modified = timezone.datetime.fromtimestamp(candidate.stat().st_mtime, tz=timezone.get_current_timezone())
            if modified < cutoff:
                candidate.unlink()
                removed += 1
        self.stdout.write(self.style.SUCCESS(f"Created {destination}. Pruned {removed} old backup(s)."))
