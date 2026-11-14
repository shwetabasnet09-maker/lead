from unittest import skipUnless

from django.test import SimpleTestCase

from leads.models import Source
from leads.scrapers import ScraperError, build_search_url, scrape_html

try:
    import bs4  # noqa: F401
    HAS_BS4 = True
except ImportError:
    HAS_BS4 = False


class ScraperTests(SimpleTestCase):
    def source(self):
        return Source(
            name="Test",
            source_type=Source.SourceType.HTML,
            base_url="https://example.com",
            search_url_template="https://example.com/search?q={query}&where={location}&page={page}",
            selectors={"item": ".result", "title": "h2", "link": "a", "snippet": "p", "date": "time"},
        )

    def test_search_url_is_encoded(self):
        url = build_search_url(self.source(), "home care", "Melbourne VIC", 2)
        self.assertIn("home+care", url)
        self.assertIn("Melbourne+VIC", url)

    def test_search_template_cannot_change_domain(self):
        source = self.source()
        source.search_url_template = "https://evil.example/{query}"
        with self.assertRaises(ScraperError):
            build_search_url(source, "home care", "Melbourne")

    @skipUnless(HAS_BS4, "beautifulsoup4 is not installed")
    def test_html_fixture(self):
        html = '<article class="result"><h2>Need home nurse</h2><a href="/post/1">Open</a><p>Looking for support in Melbourne</p><time datetime="2026-10-20T10:00:00+11:00"></time></article>'
        records = scrape_html(self.source(), "https://example.com/search", html)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].url, "https://example.com/post/1")
        self.assertEqual(records[0].title, "Need home nurse")
