from django.contrib.auth.models import User
from django.test import TestCase

from leads.classifier import _contains_word, classify_record, find_possible_duplicate
from leads.models import Keyword, Lead, Organisation, SearchProfile, ServiceCategory, Source


class ClassifierTests(TestCase):
    def setUp(self):
        self.org = Organisation.objects.create(name="Test", slug="test")
        self.profile = SearchProfile.objects.create(organisation=self.org, name="Melbourne", locations="Melbourne\nDandenong", states="VIC")
        self.category = ServiceCategory.objects.create(name="Home care", slug="home-care", synonyms="in-home care")
        Keyword.objects.create(profile=self.profile, term="home care", category=self.category)
        Keyword.objects.create(profile=self.profile, term="job", kind=Keyword.Kind.NEGATIVE)

    def test_likely_service_request(self):
        result = classify_record("Looking for home care", "Need someone in Melbourne VIC", self.profile)
        self.assertEqual(result.label, Lead.Label.LIKELY)
        self.assertEqual(result.category, self.category)
        self.assertEqual(result.state, "VIC")

    def test_job_is_not_curated(self):
        result = classify_record("Home care job", "We are hiring in Melbourne", self.profile)
        self.assertNotEqual(result.label, Lead.Label.LIKELY)

    def test_location_unknown(self):
        result = classify_record("Looking for home care", "Need help soon", self.profile)
        self.assertEqual(result.label, Lead.Label.LOCATION_UNKNOWN)

    def test_exact_service_label_ending_in_parentheses_matches(self):
        self.assertTrue(
            _contains_word(
                "Looking for Supported Independent Living (SIL) in Melbourne",
                ["Supported Independent Living (SIL)"],
            )
        )

    def test_duplicate_by_phone(self):
        source = Source.objects.create(name="Example", source_type="html", base_url="https://example.com", search_url_template="https://example.com?q={query}")
        Lead.objects.create(
            organisation=self.org,
            source=source,
            source_url="https://example.com/1",
            title="Need support",
            public_phone="0400000000",
            content_hash=Lead.make_hash("Need support", "", "0400000000"),
        )
        self.assertIsNotNone(find_possible_duplicate("Another title", "", "0400000000"))
