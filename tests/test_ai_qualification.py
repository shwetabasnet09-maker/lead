from io import StringIO
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import override_settings
from django.urls import reverse

from leads.models import Lead, Membership
from leads.services import coverage_for, ingest_record, initial_status

from .test_gemini import BaseCase, judge_response


class PriorityTests(BaseCase):
    def test_score_bands(self):
        cases = {None: "", 100: "hot", 80: "hot", 79: "warm", 50: "warm", 49: "cold", 20: "cold", 19: "irrelevant", 0: "irrelevant"}
        for score, priority in cases.items():
            self.assertEqual(Lead.priority_for(score), priority, score)

    def test_coverage(self):
        self.assertEqual(coverage_for({"suburb": "Geelong", "state": ""}, self.profile), Lead.Coverage.INSIDE)
        self.assertEqual(coverage_for({"suburb": "", "state": "VIC"}, self.profile), Lead.Coverage.INSIDE)
        self.assertEqual(coverage_for({"suburb": "Parramatta", "state": "NSW"}, self.profile), Lead.Coverage.OUTSIDE)
        self.assertEqual(coverage_for({"suburb": "", "state": ""}, self.profile), Lead.Coverage.UNKNOWN)

    def test_initial_status(self):
        self.assertEqual(initial_status({"label": "likely", "priority": ""}), Lead.Status.NEW)
        self.assertEqual(initial_status({"label": "likely", "priority": "hot"}), Lead.Status.AI_QUALIFIED)
        self.assertEqual(initial_status({"label": "review", "priority": "cold"}), Lead.Status.NEW)
        self.assertEqual(initial_status({"label": "job", "priority": "irrelevant"}), Lead.Status.DISQUALIFIED)


@override_settings(GEMINI_API_KEY="test-key", AI_LEAD_SCORING=True)
class QualificationTests(BaseCase):
    def ingest(self, **result):
        with patch("leads.gemini.requests.post", return_value=judge_response(**result)):
            lead, _, _ = ingest_record(self.source, self.profile, self.record())
        return lead

    def test_family_request_is_qualified(self):
        lead = self.ingest(intent="family", summary="Family member in Geelong asking for personal care.", relevance_score=88)
        self.assertEqual((lead.label, lead.intent, lead.priority), (Lead.Label.LIKELY, "family", Lead.Priority.HOT))
        self.assertEqual((lead.status, lead.coverage), (Lead.Status.AI_QUALIFIED, Lead.Coverage.INSIDE))
        self.assertEqual(lead.ai_summary, "Family member in Geelong asking for personal care.")

    def test_job_seeker_is_never_a_lead(self):
        # Even if the model mislabels a job seeker as likely with a high score.
        lead = self.ingest(intent="job_seeker", label="likely", relevance_score=95)
        self.assertEqual((lead.label, lead.relevance_score), (Lead.Label.JOB, 19))
        self.assertEqual((lead.priority, lead.status), (Lead.Priority.IRRELEVANT, Lead.Status.DISQUALIFIED))

    def test_provider_ad_is_never_a_lead(self):
        lead = self.ingest(intent="provider_ad", label="likely", relevance_score=90)
        self.assertEqual((lead.label, lead.status), (Lead.Label.PROVIDER_AD, Lead.Status.DISQUALIFIED))

    def test_outside_coverage_is_kept_but_not_qualified(self):
        lead = self.ingest(intent="seeking_services", suburb="Parramatta", state="NSW", postcode="2150", relevance_score=85)
        self.assertEqual(lead.coverage, Lead.Coverage.OUTSIDE)
        self.assertEqual(lead.label, Lead.Label.REVIEW)
        self.assertNotEqual(lead.status, Lead.Status.DISQUALIFIED)

    def test_prompt_lists_intents_and_forbids_invented_details(self):
        with patch("leads.gemini.requests.post", return_value=judge_response(intent="family")) as post:
            ingest_record(self.source, self.profile, self.record())
        body = post.call_args.kwargs["json"]
        self.assertIn("job_seeker", body["input"])
        self.assertIn("Never invent names, contact details", body["input"])
        self.assertIn("intent", body["response_format"]["schema"]["required"])


@override_settings(GEMINI_API_KEY="test-key", AI_LEAD_SCORING=True)
class ListAndCommandTests(BaseCase):
    def setUp(self):
        super().setUp()
        self.user = User.objects.create_user("staff", password="pw-123456789")
        Membership.objects.create(organisation=self.org, user=self.user, role=Membership.Role.OWNER)
        self.client.force_login(self.user)

    def test_disqualified_hidden_unless_asked_for(self):
        with patch("leads.gemini.requests.post", return_value=judge_response(intent="job_seeker")):
            lead, _, _ = ingest_record(self.source, self.profile, self.record())
        self.assertNotContains(self.client.get(reverse("lead-list")), lead.title)
        self.assertContains(self.client.get(reverse("lead-list"), {"status": "disqualified"}), lead.title)

    def test_priority_filter(self):
        with patch("leads.gemini.requests.post", return_value=judge_response(intent="family", relevance_score=90)):
            hot, _, _ = ingest_record(self.source, self.profile, self.record())
        self.assertContains(self.client.get(reverse("lead-list"), {"priority": "hot"}), hot.title)
        self.assertNotContains(self.client.get(reverse("lead-list"), {"priority": "warm"}), hot.title)

    def test_qualify_command_scores_unscored_leads_and_keeps_staff_status(self):
        with override_settings(GEMINI_API_KEY=""):
            lead, _, _ = ingest_record(self.source, self.profile, self.record())
        lead.status = Lead.Status.CONTACTED
        lead.save()
        with patch("leads.gemini.requests.post", return_value=judge_response(intent="family", relevance_score=70)):
            call_command("qualify_leads", stdout=StringIO())
        lead.refresh_from_db()
        self.assertEqual((lead.relevance_score, lead.priority, lead.intent), (70, Lead.Priority.WARM, "family"))
        self.assertEqual(lead.status, Lead.Status.CONTACTED)
