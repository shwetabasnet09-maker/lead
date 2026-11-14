from unittest import mock

from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.urls import reverse

from leads import jobs
from leads.models import Keyword, Membership, Organisation, SearchProfile, ServiceCategory, Source
from leads.services import ScanAlreadyRunning


NO_LOCK = override_settings(SCRAPER_LOCK_FILE="/nonexistent/ndis-test.lock")


@NO_LOCK
class SettingsScreenTests(TestCase):
    def setUp(self):
        self.org = Organisation.objects.create(name="Test", slug="test")
        self.manager = User.objects.create_user("manager", password="safe-test-password")
        Membership.objects.create(organisation=self.org, user=self.manager, role=Membership.Role.MANAGER)
        self.employee = User.objects.create_user("employee", password="safe-test-password")
        Membership.objects.create(organisation=self.org, user=self.employee, role=Membership.Role.EMPLOYEE)
        self.source = Source.objects.create(
            name="Feed", source_type="rss", base_url="https://example.com", search_url_template="https://example.com/feed",
            review_status=Source.ReviewStatus.APPROVED, robots_reviewed=True, terms_reviewed=True, enabled=True,
        )
        self.client.force_login(self.manager)

    def test_manager_pages_render(self):
        membership = Membership.objects.get(user=self.employee)
        pages = [
            ("settings-sources", []), ("settings-source-new", []), ("settings-source-edit", [self.source.pk]),
            ("settings-search", []), ("settings-services", []), ("settings-team", []), ("settings-team-new", []),
            ("settings-team-member", [membership.pk]), ("dashboard", []),
        ]
        for name, args in pages:
            with self.subTest(name=name):
                self.assertEqual(self.client.get(reverse(name, args=args)).status_code, 200)

    def test_employee_cannot_open_settings_or_see_link(self):
        self.client.force_login(self.employee)
        self.assertEqual(self.client.get(reverse("settings-sources")).status_code, 403)
        self.assertEqual(self.client.post(reverse("settings-scan")).status_code, 403)
        self.assertNotContains(self.client.get(reverse("dashboard")), reverse("settings-sources"))

    def test_source_cannot_be_switched_on_before_review(self):
        pending = Source.objects.create(name="New site", source_type="html", base_url="https://site.test", search_url_template="https://site.test/?q={query}")
        self.client.post(reverse("settings-source-toggle", args=[pending.pk]))
        pending.refresh_from_db()
        self.assertFalse(pending.enabled)
        response = self.client.post(
            reverse("settings-source-edit", args=[pending.pk]),
            {"name": "New site", "source_type": "html", "base_url": "https://site.test", "search_url_template": "https://site.test/?q={query}",
             "selectors": "", "review_status": "pending", "enabled": "on", "request_delay_seconds": 3, "max_pages": 1},
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Approve the source")

    def test_toggle_switches_reviewed_source_off(self):
        self.client.post(reverse("settings-source-toggle", args=[self.source.pk]))
        self.source.refresh_from_db()
        self.assertFalse(self.source.enabled)

    def test_invalid_selector_json_is_explained(self):
        data = {"name": "Feed", "source_type": "html", "base_url": "https://example.com", "search_url_template": "https://example.com/?q={query}",
                "selectors": "{not json", "review_status": "pending", "request_delay_seconds": 3, "max_pages": 1}
        response = self.client.post(reverse("settings-source-edit", args=[self.source.pk]), data)
        self.assertContains(response, "not valid JSON")

    @mock.patch("leads.settings_views.start_scan")
    def test_scan_now_starts_a_scan(self, start_scan):
        response = self.client.post(reverse("settings-scan"))
        self.assertRedirects(response, reverse("settings-sources"))
        start_scan.assert_called_once_with(None)

    @mock.patch("leads.settings_views.start_scan", side_effect=ScanAlreadyRunning("A scan is already running."))
    def test_scan_now_reports_a_running_scan(self, _):
        response = self.client.post(reverse("settings-scan"), follow=True)
        self.assertContains(response, "already running")

    @mock.patch("leads.settings_views.start_scan")
    def test_scan_now_ignores_offsite_next(self, _):
        response = self.client.post(reverse("settings-scan"), {"next": "https://evil.example/"})
        self.assertRedirects(response, reverse("settings-sources"))

    def test_search_area_and_keywords(self):
        self.client.post(reverse("settings-search"), {"form": "profile", "states": "vic, nsw", "locations": "Geelong", "radius_km": 30, "freshness_days": 5, "daily_lead_limit": 8})
        profile = SearchProfile.objects.get(organisation=self.org)
        self.assertEqual(profile.states, "VIC, NSW")
        self.client.post(reverse("settings-search"), {"form": "keyword", "term": "need a carer", "kind": "positive"})
        keyword = Keyword.objects.get(profile=profile)
        response = self.client.post(reverse("settings-search"), {"form": "keyword", "term": "Need a carer", "kind": "positive"})
        self.assertContains(response, "already in the list")
        self.client.post(reverse("settings-keyword-toggle", args=[keyword.pk]))
        keyword.refresh_from_db()
        self.assertFalse(keyword.enabled)
        self.client.post(reverse("settings-keyword-delete", args=[keyword.pk]))
        self.assertFalse(Keyword.objects.exists())

    def test_unknown_state_is_rejected(self):
        response = self.client.post(reverse("settings-search"), {"form": "profile", "states": "Victoria", "radius_km": 30, "freshness_days": 5, "daily_lead_limit": 8})
        self.assertContains(response, "Unknown state")

    def test_add_service_creates_unique_slug(self):
        ServiceCategory.objects.create(name="Respite", slug="respite")
        self.client.post(reverse("settings-services"), {"name": "Respite ", "synonyms": "", "active": "on"})
        self.client.post(reverse("settings-services"), {"name": "Respite care", "synonyms": "short stay", "active": "on"})
        self.assertTrue(ServiceCategory.objects.filter(slug="respite-care").exists())

    def test_manager_adds_team_member_but_not_an_owner(self):
        data = {"first_name": "Sam", "username": "sam", "role": "employee", "password": "a-long-safe-passphrase", "active": "on"}
        self.client.post(reverse("settings-team-new"), data)
        sam = User.objects.get(username="sam")
        self.assertTrue(sam.check_password("a-long-safe-passphrase"))
        self.assertEqual(Membership.objects.get(user=sam).role, Membership.Role.EMPLOYEE)
        response = self.client.post(reverse("settings-team-new"), {**data, "username": "boss", "role": "owner"})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(User.objects.filter(username="boss").exists())

    def test_manager_cannot_lock_themselves_out(self):
        membership = Membership.objects.get(user=self.manager)
        response = self.client.post(reverse("settings-team-member", args=[membership.pk]), {"first_name": "M", "username": "manager", "role": "employee", "active": "on"})
        self.assertContains(response, "cannot remove your own access")
        membership.refresh_from_db()
        self.assertEqual(membership.role, Membership.Role.MANAGER)

    def test_manager_cannot_edit_owner(self):
        owner = User.objects.create_user("owner", password="safe-test-password")
        membership = Membership.objects.create(organisation=self.org, user=owner, role=Membership.Role.OWNER)
        self.assertEqual(self.client.get(reverse("settings-team-member", args=[membership.pk])).status_code, 403)

    def test_scan_status_json(self):
        response = self.client.get(reverse("settings-scan-status"))
        self.assertEqual(response.json()["running"], False)


@NO_LOCK
class StartScanTests(TestCase):
    @override_settings(CELERY_BROKER_URL="")
    @mock.patch("leads.jobs.subprocess.Popen")
    def test_without_redis_starts_a_process(self, popen):
        self.assertEqual(jobs.start_scan(3), "process")
        self.assertEqual(popen.call_args.args[0][-3:], ["run_scrapers", "--source", "3"])

    @override_settings(CELERY_BROKER_URL="redis://127.0.0.1:6379/0")
    @mock.patch("leads.tasks.run_scan.delay")
    def test_with_redis_queues_on_celery(self, delay):
        self.assertEqual(jobs.start_scan(), "celery")
        delay.assert_called_once_with(None)

    @override_settings(CELERY_BROKER_URL="redis://127.0.0.1:6379/0")
    @mock.patch("leads.jobs.subprocess.Popen")
    @mock.patch("leads.tasks.run_scan.delay", side_effect=ConnectionError("down"))
    def test_falls_back_when_redis_is_down(self, _, popen):
        self.assertEqual(jobs.start_scan(), "process")
        popen.assert_called_once()

    @mock.patch("leads.jobs.scan_is_running", return_value=True)
    def test_refuses_while_running(self, _):
        with self.assertRaises(ScanAlreadyRunning):
            jobs.start_scan()
