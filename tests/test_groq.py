import json
from unittest.mock import MagicMock, patch

from django.contrib.auth.models import User
from django.test import override_settings
from django.urls import reverse

from leads import groq
from leads.models import Lead, Membership
from leads.services import ingest_record
from tests.test_gemini import BaseCase, api_response


def groq_response(status=200, headers=None, **overrides):
    result = {
        "label": "likely", "lead_type": "family", "service_category": "Personal care", "suburb": "Geelong",
        "state": "VIC", "postcode": "3220", "relevance_score": 88, "reason": "Family member asking for care.",
        **overrides,
    }
    response = api_response({"choices": [{"message": {"content": json.dumps(result)}}]}, status)
    response.headers = headers or {}
    return response


def rate_limited(retry_after="2"):
    response = api_response({"error": {"message": "rate limit"}}, 429)
    response.headers = {"retry-after": retry_after}
    return response


def search_response(content, urls=("https://forum.example/real",)):
    results = [{"title": "Real post", "url": url, "content": "text", "score": 1} for url in urls]
    message = {"role": "assistant", "content": content, "executed_tools": [{"type": "browser_search", "search_results": {"results": results}}]}
    return api_response({"choices": [{"message": message}]})


@override_settings(GROQ_API_KEY="groq-test-key", GROQ_MODEL="openai/gpt-oss-20b")
class GroqSearchPageTests(BaseCase):
    def setUp(self):
        super().setUp()
        self.user = User.objects.create_user("staff", password="x")
        Membership.objects.create(organisation=self.org, user=self.user)
        self.client.force_login(self.user)
        self.form = {"service": "Personal care", "location": "Melbourne, VIC"}

    def search(self, response):
        with patch("leads.groq.requests.post", return_value=response) as post:
            return self.client.post(reverse("ai-search"), self.form), post

    @override_settings(GEMINI_API_KEY="gemini-test-key")
    def test_groq_is_used_when_both_keys_are_set(self):
        response, post = self.search(search_response("One post: https://forum.example/real."))
        self.assertEqual(post.call_args.kwargs["json"]["tools"], [{"type": "browser_search"}])
        self.assertContains(response, "Groq's answer")
        self.assertContains(response, '<a href="https://forum.example/real"', count=1)
        self.assertEqual(Lead.objects.count(), 0)

    def test_service_can_be_typed_and_existing_ones_are_suggested(self):
        self.assertContains(self.client.get(reverse("ai-search")), '<option value="Personal care">')
        self.form["service"] = "Respite for a teenager"
        response, post = self.search(search_response("Nothing found."))
        self.assertContains(response, "Nothing found.")
        self.assertIn("Respite for a teenager", post.call_args.kwargs["json"]["messages"][0]["content"])

    def test_invented_link_is_removed(self):
        response, _ = self.search(search_response("Made up: https://invented.example/post and real: https://forum.example/real/"))
        self.assertNotContains(response, "invented.example")
        self.assertContains(response, "link removed")
        self.assertContains(response, '<a href="https://forum.example/real"', count=1)

    def test_searched_pages_not_used_in_the_answer_are_not_listed(self):
        response, _ = self.search(search_response("I could not find any matching posts."))
        self.assertContains(response, "could not find")
        self.assertNotContains(response, "forum.example")

    def test_citation_markers_are_removed(self):
        response, _ = self.search(search_response("A post in Geelong【2†L6-L10】."))
        self.assertContains(response, "A post in Geelong.")

    def test_per_minute_limit_waits_for_refill_then_retries(self):
        success = search_response("One post: https://forum.example/real")
        with patch("leads.groq.requests.post", side_effect=[rate_limited("2"), success]), patch("leads.groq.time.sleep") as sleep:
            response = self.client.post(reverse("ai-search"), self.form)
        sleep.assert_called_once_with(groq.SEARCH_MIN_RETRY_WAIT_SECONDS)
        self.assertContains(response, "One post")

    def test_still_limited_shows_a_plain_message(self):
        with patch("leads.groq.requests.post", return_value=rate_limited("2")), patch("leads.groq.time.sleep"):
            response = self.client.post(reverse("ai-search"), self.form)
        self.assertContains(response, "wait a minute")
        self.assertNotContains(response, "org_")

    def test_exhausted_limit_does_not_wait(self):
        with patch("leads.groq.requests.post", return_value=rate_limited("3600")) as post, patch("leads.groq.time.sleep") as sleep:
            response = self.client.post(reverse("ai-search"), self.form)
        sleep.assert_not_called()
        self.assertEqual(post.call_count, 1)
        self.assertContains(response, "try again later")


@override_settings(GROQ_API_KEY="groq-test-key", GROQ_MODEL="openai/gpt-oss-20b")
class GroqScoringTests(BaseCase):
    def test_score_is_stored_and_request_is_strict(self):
        with patch("leads.groq.requests.post", return_value=groq_response()) as post:
            lead, created, _ = ingest_record(self.source, self.profile, self.record())
        self.assertTrue(created)
        self.assertEqual((lead.label, lead.relevance_score), (Lead.Label.LIKELY, 88))
        self.assertEqual(post.call_args.kwargs["headers"]["Authorization"], "Bearer groq-test-key")
        body = post.call_args.kwargs["json"]
        self.assertEqual(body["model"], "openai/gpt-oss-20b")
        schema = body["response_format"]["json_schema"]
        self.assertTrue(schema["strict"])
        self.assertFalse(schema["schema"]["additionalProperties"])
        self.assertEqual(set(schema["schema"]["required"]), set(schema["schema"]["properties"]))

    @override_settings(GEMINI_API_KEY="gemini-test-key")
    def test_groq_scores_when_both_keys_are_set(self):
        with patch("leads.groq.requests.post", return_value=groq_response()) as post:
            ingest_record(self.source, self.profile, self.record())
        self.assertEqual(post.call_args.args[0], groq.API_URL)

    def test_rate_limit_waits_then_retries(self):
        with patch("leads.groq.requests.post", side_effect=[rate_limited("2"), groq_response()]) as post, patch("leads.groq.time.sleep") as sleep:
            lead, _, _ = ingest_record(self.source, self.profile, self.record())
        self.assertEqual(post.call_count, 2)
        sleep.assert_called_once_with(2)
        self.assertEqual(lead.relevance_score, 88)

    def test_exhausted_limit_does_not_hold_up_the_scraper(self):
        with patch("leads.groq.requests.post", return_value=rate_limited("3600")) as post, patch("leads.groq.time.sleep") as sleep:
            lead, created, _ = ingest_record(self.source, self.profile, self.record())
        sleep.assert_not_called()
        self.assertEqual(post.call_count, 1)
        self.assertTrue(created)
        self.assertIsNone(lead.relevance_score)

    def test_failures_keep_the_lead_without_a_score(self):
        malformed = MagicMock(status_code=200, ok=True, headers={})
        malformed.json.return_value = {"choices": []}
        for name, side_effect in [
            ("still limited", [rate_limited(), rate_limited()]),
            ("malformed", [malformed]),
            ("bad label", [groq_response(label="approved")]),
            ("missing field", [api_response({"choices": [{"message": {"content": "{}"}}]})]),
        ]:
            with self.subTest(name), patch("leads.groq.requests.post", side_effect=side_effect), patch("leads.groq.time.sleep"):
                lead, created, _ = ingest_record(self.source, self.profile, self.record(f"https://forum.example/{name.replace(' ', '-')}", title=f"Need personal care in Geelong, {name}"))
                self.assertTrue(created)
                self.assertIsNone(lead.relevance_score)
