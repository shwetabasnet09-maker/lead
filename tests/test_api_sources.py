from unittest.mock import MagicMock, patch

from django.test import TestCase, override_settings

from leads.api_sources import SERVICE_PHRASES, api_requests, fetch_api_source, fetch_bluesky, fetch_brave, fetch_mastodon
from leads.models import Keyword, Organisation, SearchProfile, ServiceCategory, Source
from leads.scrapers import ScraperError


def fake_session(payload):
    session = MagicMock()
    session.get.return_value.json.return_value = payload
    session.post.return_value.json.return_value = {"accessJwt": "token"}
    return session


class ApiSourceTests(TestCase):
    def setUp(self):
        organisation = Organisation.objects.create(name="Org", slug="org")
        self.profile = SearchProfile.objects.create(organisation=organisation, locations="Melbourne\nGeelong")
        Keyword.objects.create(profile=self.profile, term="support worker")

    def source(self, source_type, config):
        return Source(
            name="API",
            source_type=source_type,
            base_url="https://api.example.com/",
            search_url_template="https://api.example.com/",
            selectors=config,
            enabled=True,
            review_status=Source.ReviewStatus.APPROVED,
            robots_reviewed=True,
            terms_reviewed=True,
        )

    def test_location_placeholder_expands_per_location(self):
        source = self.source(Source.SourceType.BRAVE, {"queries": ["ndis {location}", "ndis help"]})
        self.assertEqual(api_requests(source, self.profile), ["ndis Melbourne", "ndis Geelong", "ndis help"])

    def test_service_placeholder_expands_per_service(self):
        source = self.source(Source.SourceType.BLUESKY, {"queries": ['ndis "{service}"']})
        requests = api_requests(source, self.profile)
        self.assertEqual(len(requests), len(SERVICE_PHRASES))
        self.assertIn('ndis "life skills"', requests)

    def test_service_and_location_placeholders_combine(self):
        source = self.source(Source.SourceType.BLUESKY, {"queries": ["{service} {location}"], "services": ["sil", "ot"]})
        self.assertEqual(api_requests(source, self.profile), ["sil Melbourne", "sil Geelong", "ot Melbourne", "ot Geelong"])

    @override_settings(BRAVE_API_KEY="key")
    def test_category_synonyms_count_as_service_keywords(self):
        ServiceCategory.objects.create(name="Supported Independent Living", slug="sil", synonyms="sil")
        payload = {"web": {"results": [
            {"title": "Looking for SIL in Geelong", "url": "https://forum.example/sil", "description": ""},
        ]}}
        source = self.source(Source.SourceType.BRAVE, {"queries": ["ndis"]})
        with patch("leads.api_sources._session", return_value=fake_session(payload)):
            results = list(fetch_api_source(source, self.profile))
        self.assertEqual([r.url for r in results[0][1]], ["https://forum.example/sil"])

    def test_missing_config_is_reported(self):
        with self.assertRaises(ScraperError):
            api_requests(self.source(Source.SourceType.MASTODON, {}), self.profile)

    def test_mastodon_parses_public_statuses(self):
        payload = [
            {"id": "1", "url": "https://m.example/@a/1", "visibility": "public", "content": "<p>Looking for a support worker</p>", "created_at": "2026-10-21T14:34:29.612Z", "account": {"acct": "a"}},
            {"id": "2", "url": "https://m.example/@b/2", "visibility": "unlisted", "content": "<p>hidden</p>"},
        ]
        with patch("leads.api_sources._session", return_value=fake_session(payload)):
            records = fetch_mastodon(self.source(Source.SourceType.MASTODON, {}), "ndis")
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].title, "Looking for a support worker")
        self.assertIsNotNone(records[0].published_at)

    def test_bluesky_builds_web_url(self):
        payload = {"posts": [{"uri": "at://did:plc:x/app.bsky.feed.post/abc", "author": {"handle": "sam.bsky.social"}, "record": {"text": "Need a support worker", "createdAt": "2026-10-20T00:00:00Z"}}]}
        with patch("leads.api_sources._session", return_value=fake_session(payload)):
            records = fetch_bluesky(self.source(Source.SourceType.BLUESKY, {}), "ndis", "token")
        self.assertEqual(records[0].url, "https://bsky.app/profile/sam.bsky.social/post/abc")

    @override_settings(BRAVE_API_KEY="")
    def test_brave_requires_key(self):
        with self.assertRaises(ScraperError):
            fetch_brave(self.source(Source.SourceType.BRAVE, {}), "ndis")

    @override_settings(BRAVE_API_KEY="key")
    def test_only_service_requests_are_kept(self):
        payload = {"web": {"results": [
            {"title": "Looking for a <strong>support worker</strong> in Melbourne", "url": "https://forum.example/1", "description": "Can anyone help?"},
            {"title": "NDIS funding changes announced", "url": "https://news.example/2", "description": "Policy news"},
        ]}}
        source = self.source(Source.SourceType.BRAVE, {"queries": ["ndis"]})
        with patch("leads.api_sources._session", return_value=fake_session(payload)), patch("leads.services.polite_pause"):
            results = list(fetch_api_source(source, self.profile))
        self.assertEqual([r.url for r in results[0][1]], ["https://forum.example/1"])
        self.assertEqual(results[0][1][0].title, "Looking for a support worker in Melbourne")


class BlueskyWithoutAccountTests(TestCase):
    def test_uses_public_search_without_login(self):
        from unittest import mock

        source = Source(name="Bluesky", source_type=Source.SourceType.BLUESKY, base_url="https://bsky.social/", search_url_template="https://bsky.social/")
        with mock.patch("leads.api_sources._session") as session:
            session.return_value.get.return_value.json.return_value = {"posts": []}
            fetch_bluesky(source, "ndis", "")
        args, kwargs = session.return_value.get.call_args
        self.assertTrue(args[0].startswith("https://api.bsky.app/"))
        self.assertEqual(kwargs["headers"], {})
