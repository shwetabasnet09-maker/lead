from django.conf import settings
from django.core.mail import send_mail
from django.core.management.base import BaseCommand
from django.utils import timezone

from leads.models import Lead, Membership, Organisation


class Command(BaseCommand):
    help = "Email a daily lead summary to owners and managers."

    def handle(self, *args, **options):
        today = timezone.localdate()
        sent = 0
        for organisation in Organisation.objects.filter(active=True):
            new_count = Lead.objects.filter(organisation=organisation, discovered_at__date=today).count()
            likely_count = Lead.objects.filter(organisation=organisation, discovered_at__date=today, label=Lead.Label.LIKELY).count()
            recipients = list(
                organisation.memberships.filter(active=True, role__in=[Membership.Role.OWNER, Membership.Role.MANAGER])
                .exclude(user__email="")
                .values_list("user__email", flat=True)
            )
            if recipients:
                send_mail(
                    f"{settings.APP_NAME}: daily lead summary",
                    f"{new_count} new leads were discovered today. {likely_count} were marked as likely service requests.",
                    settings.DEFAULT_FROM_EMAIL,
                    recipients,
                )
                sent += len(recipients)
        self.stdout.write(self.style.SUCCESS(f"Sent {sent} summary email(s)."))
