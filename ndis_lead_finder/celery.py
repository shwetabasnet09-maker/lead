"""Celery runs scans, backups and the daily summary in the background when REDIS_URL is set.

Start a worker with its scheduler (one process only, because SQLite allows one writer):
    celery -A ndis_lead_finder worker --beat --concurrency 1 --loglevel info
On Windows, add --pool solo.
"""
import os

from celery import Celery

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "ndis_lead_finder.settings")

app = Celery("ndis_lead_finder")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()
