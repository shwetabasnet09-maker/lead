from celery import shared_task
from django.core.management import call_command

from .services import ScanAlreadyRunning, run_all_sources


@shared_task(name="leads.run_scan", ignore_result=True)
def run_scan(source_id: int | None = None):
    try:
        return run_all_sources(source_id)
    except ScanAlreadyRunning:
        return {"skipped": "already running"}


@shared_task(name="leads.backup_database", ignore_result=True)
def backup_database():
    call_command("backup_database")


@shared_task(name="leads.send_daily_summary", ignore_result=True)
def send_daily_summary():
    call_command("send_daily_summary")
