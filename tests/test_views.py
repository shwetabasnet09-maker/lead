from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.urls import reverse

from leads.models import Lead, LoginThrottle, Membership, Organisation, Source


class LeadWorkflowTests(TestCase):
    def setUp(self):
        self.org = Organisation.objects.create(name="Test", slug="test")
        self.user = User.objects.create_user("employee", password="safe-test-password")
        Membership.objects.create(organisation=self.org, user=self.user, role=Membership.Role.EMPLOYEE)
        self.source = Source.objects.create(name="Source", source_type="html", base_url="https://example.com", search_url_template="https://example.com")
        self.lead = Lead.objects.create(
            organisation=self.org,
            source=self.source,
            source_url="https://example.com/post",
            title="Looking for a support worker in Melbourne",
            content_hash=Lead.make_hash("Looking for a support worker in Melbourne", ""),
        )
        self.client.login(username="employee", password="safe-test-password")

    def test_lead_list_requires_login(self):
        self.client.logout()
        response = self.client.get(reverse("lead-list"))
        self.assertEqual(response.status_code, 302)

    def test_employee_can_claim_lead(self):
        response = self.client.post(reverse("lead-claim", args=[self.lead.pk]))
        self.assertRedirects(response, reverse("lead-detail", args=[self.lead.pk]))
        self.lead.refresh_from_db()
        self.assertEqual(self.lead.claimed_by, self.user)

    def test_employee_can_update_claimed_lead(self):
        self.lead.claimed_by = self.user
        self.lead.save()
        response = self.client.post(reverse("lead-update", args=[self.lead.pk]), {"status": Lead.Status.CONTACTED, "note": "Verified and contacted."})
        self.assertRedirects(response, reverse("lead-detail", args=[self.lead.pk]))
        self.lead.refresh_from_db()
        self.assertEqual(self.lead.status, Lead.Status.CONTACTED)
        self.assertEqual(self.lead.activities.count(), 1)

    def test_csv_export(self):
        response = self.client.get(reverse("lead-export"))
        self.assertEqual(response.status_code, 200)
        self.assertIn("text/csv", response["Content-Type"])

    def test_staff_pages_render(self):
        for name, args in [("dashboard", []), ("lead-list", []), ("lead-detail", [self.lead.pk]), ("reports", []), ("notifications", [])]:
            with self.subTest(name=name):
                response = self.client.get(reverse(name, args=args))
                self.assertEqual(response.status_code, 200)

    def test_unfold_admin_renders(self):
        self.user.is_staff = True
        self.user.is_superuser = True
        self.user.save()
        response = self.client.get(reverse("admin:index"))
        self.assertEqual(response.status_code, 200)

    def test_login_is_temporarily_locked_after_repeated_failures(self):
        self.client.logout()
        for _ in range(5):
            self.client.post(reverse("login"), {"username": "employee", "password": "wrong-password"})
        throttle = LoginThrottle.objects.get()
        self.assertIsNotNone(throttle.locked_until)
        response = self.client.post(reverse("login"), {"username": "employee", "password": "safe-test-password"})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.wsgi_request.user.is_authenticated)

    def test_user_without_membership_cannot_view_default_organisation(self):
        outsider = User.objects.create_user("outsider", password="safe-test-password")
        self.client.force_login(outsider)
        response = self.client.get(reverse("lead-list"))
        self.assertEqual(response.status_code, 403)


@override_settings(SCRAPER_LOCK_FILE="/nonexistent/ndis-test.lock")
class EmptyLeadListTests(TestCase):
    def setUp(self):
        from datetime import timedelta
        from django.utils import timezone

        self.org = Organisation.objects.create(name="Test", slug="test")
        self.user = User.objects.create_user("employee", password="safe-test-password")
        Membership.objects.create(organisation=self.org, user=self.user, role=Membership.Role.EMPLOYEE)
        source = Source.objects.create(name="Source", source_type="html", base_url="https://example.com", search_url_template="https://example.com")
        old = timezone.now() - timedelta(days=30)
        Lead.objects.create(organisation=self.org, source=source, source_url="https://example.com/old", title="Old request", published_at=old, discovered_at=old, content_hash="x")
        self.client.force_login(self.user)

    def test_empty_recent_list_offers_older_leads(self):
        response = self.client.get(reverse("lead-list"))
        self.assertContains(response, "No leads posted in the last 7 days")
        self.assertContains(response, "1 older lead matches")
        self.assertContains(response, "posted=all")
        self.assertContains(self.client.get(reverse("lead-list"), {"posted": "all"}), "Old request")

    def test_dashboard_warns_when_no_recent_scan(self):
        self.assertContains(self.client.get(reverse("dashboard")), "Leads are out of date")
