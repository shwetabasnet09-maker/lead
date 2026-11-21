from __future__ import annotations

import logging
import re
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime
from email.utils import parsedate_to_datetime
from urllib.parse import parse_qs, quote_plus, urljoin, urlparse
from urllib.robotparser import RobotFileParser

import requests
from django.conf import settings
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from .models import Source

logger = logging.getLogger(__name__)


@dataclass
class ScrapedRecord:
    title: str
    url: str
    snippet: str = ""
    published_at: datetime | None = None
    public_name: str = ""
    public_phone: str = ""
    public_email: str = ""
    external_id: str = ""


class ScraperError(RuntimeError):
    pass


def build_search_url(source: Source, query: str, location: str, page: int = 1) -> str:
    url = source.search_url_template.format(query=quote_plus(query), location=quote_plus(location), page=page)
    if urlparse(url).netloc.lower() != urlparse(source.base_url).netloc.lower():
        raise ScraperError("Search URL template must remain on the approved Base URL domain")
    return url


def robots_allowed(source: Source, url: str) -> bool:
    parsed = urlparse(source.base_url)
    robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
    parser = RobotFileParser(robots_url)
    try:
        parser.read()
        return parser.can_fetch(settings.SCRAPER_USER_AGENT, url)
    except Exception as exc:
        logger.warning("Could not read robots.txt for %s: %s", source.name, exc)
        return False


def _session() -> requests.Session:
    session = requests.Session()
    session.headers.update({"User-Agent": settings.SCRAPER_USER_AGENT, "Accept-Language": "en-AU,en;q=0.9"})
    return session


def _parse_date(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = parse_datetime(value)
        if parsed:
            return parsed if timezone.is_aware(parsed) else timezone.make_aware(parsed)
        parsed = parsedate_to_datetime(value)
        return parsed if timezone.is_aware(parsed) else timezone.make_aware(parsed)
    except (TypeError, ValueError, OverflowError):
        return None


def scrape_rss(source: Source, url: str) -> list[ScrapedRecord]:
    response = _session().get(url, timeout=settings.SCRAPER_TIMEOUT_SECONDS)
    response.raise_for_status()
    root = ET.fromstring(response.content)
    records: list[ScrapedRecord] = []
    items = root.findall(".//item")
    if items:
        for item in items:
            title = item.findtext("title", "").strip()
            link = item.findtext("link", "").strip()
            snippet = item.findtext("description", "").strip()
            published = item.findtext("pubDate", "")
            guid = item.findtext("guid", "")
            if title and link:
                records.append(ScrapedRecord(title, link, _strip_html(snippet), _parse_date(published), external_id=guid))
        return records
    ns = {"atom": "http://www.w3.org/2005/Atom"}
    for entry in root.findall(".//atom:entry", ns):
        title = entry.findtext("atom:title", "", ns).strip()
        link_node = entry.find("atom:link", ns)
        link = link_node.attrib.get("href", "") if link_node is not None else ""
        snippet = entry.findtext("atom:summary", "", ns) or entry.findtext("atom:content", "", ns)
        published = entry.findtext("atom:published", "", ns) or entry.findtext("atom:updated", "", ns)
        external_id = entry.findtext("atom:id", "", ns)
        if title and link:
            records.append(ScrapedRecord(title, link, _strip_html(snippet), _parse_date(published), external_id=external_id))
    return records


def _unwrap_redirect(url: str) -> str:
    """Search engines link through a redirect. Store the address of the post itself."""
    parsed = urlparse(url)
    if parsed.netloc.endswith("duckduckgo.com") and parsed.path.startswith("/l/"):
        target = parse_qs(parsed.query).get("uddg")
        if target:
            return target[0]
    return url


def _strip_html(value: str) -> str:
    try:
        from bs4 import BeautifulSoup

        return BeautifulSoup(value, "html.parser").get_text(" ", strip=True)
    except ImportError:
        return re.sub(r"<[^>]+>", " ", value).strip()


def scrape_html(source: Source, url: str, html: str | None = None) -> list[ScrapedRecord]:
    try:
        from bs4 import BeautifulSoup
    except ImportError as exc:
        raise ScraperError("beautifulsoup4 is required for HTML sources") from exc
    if html is None:
        response = _session().get(url, timeout=settings.SCRAPER_TIMEOUT_SECONDS)
        response.raise_for_status()
        html = response.text
    soup = BeautifulSoup(html, "html.parser")
    selectors = source.selectors or {}
    item_selector = selectors.get("item")
    if not item_selector:
        raise ScraperError("HTML source requires an 'item' CSS selector")
    records: list[ScrapedRecord] = []
    for node in soup.select(item_selector):
        title_node = node.select_one(selectors.get("title", "h1, h2, h3, a"))
        link_node = node.select_one(selectors.get("link", "a[href]"))
        if not title_node or not link_node:
            continue
        title = title_node.get_text(" ", strip=True)
        link = _unwrap_redirect(urljoin(url, link_node.get("href", "")))
        snippet_node = node.select_one(selectors.get("snippet", "p"))
        date_node = node.select_one(selectors.get("date", "time"))
        name_node = node.select_one(selectors["name"]) if selectors.get("name") else None
        phone_node = node.select_one(selectors["phone"]) if selectors.get("phone") else None
        email_node = node.select_one(selectors["email"]) if selectors.get("email") else None
        date_value = ""
        if date_node:
            date_value = date_node.get("datetime", "") or date_node.get_text(" ", strip=True)
        if title and link:
            records.append(
                ScrapedRecord(
                    title=title,
                    url=link,
                    snippet=snippet_node.get_text(" ", strip=True) if snippet_node else "",
                    published_at=_parse_date(date_value),
                    public_name=name_node.get_text(" ", strip=True) if name_node else "",
                    public_phone=phone_node.get_text(" ", strip=True) if phone_node else "",
                    public_email=email_node.get_text(" ", strip=True) if email_node else "",
                )
            )
    return records


def open_browser():
    """Start headless Chrome. Selenium Manager downloads a matching driver if needed."""
    if not settings.SELENIUM_ENABLED:
        raise ScraperError("Selenium is disabled. Set SELENIUM_ENABLED=true once Chrome or Chromium is installed")
    try:
        from selenium import webdriver
    except ImportError as exc:
        raise ScraperError("selenium package is not installed") from exc
    options = webdriver.ChromeOptions()
    options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--window-size=1280,2000")
    options.add_argument(f"--user-agent={settings.SCRAPER_USER_AGENT}")
    driver = webdriver.Chrome(options=options)
    driver.set_page_load_timeout(settings.SCRAPER_TIMEOUT_SECONDS)
    return driver


def scrape_selenium(source: Source, url: str, driver=None) -> list[ScrapedRecord]:
    """Render the page in Chrome, then read it with the source's CSS selectors.

    Pass ``driver`` to reuse one browser for several pages. Selectors may include
    "empty", matched on a page that loaded correctly but has no results.
    """
    from selenium.common.exceptions import TimeoutException
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support.ui import WebDriverWait

    selectors = source.selectors or {}
    item_selector = selectors.get("item")
    if not item_selector:
        raise ScraperError("Selenium source requires an 'item' CSS selector")
    empty_selector = selectors.get("empty")
    own_driver = driver is None
    if own_driver:
        driver = open_browser()
    try:
        driver.get(url)

        def loaded(browser):
            if browser.find_elements(By.CSS_SELECTOR, item_selector):
                return "items"
            if empty_selector and browser.find_elements(By.CSS_SELECTOR, empty_selector):
                return "empty"
            return False

        try:
            state = WebDriverWait(driver, settings.SCRAPER_TIMEOUT_SECONDS).until(loaded)
        except TimeoutException as exc:
            raise ScraperError(f"{source.name} did not show results. The site may be blocking automated browsers") from exc
        return [] if state == "empty" else scrape_html(source, url, driver.page_source)
    finally:
        if own_driver:
            driver.quit()


def scrape_source(source: Source, url: str, driver=None) -> list[ScrapedRecord]:
    if not source.is_runnable:
        raise ScraperError("Source is disabled or has not completed permission review")
    if not robots_allowed(source, url):
        raise ScraperError("robots.txt does not allow this request or could not be verified")
    if source.source_type == Source.SourceType.RSS:
        return scrape_rss(source, url)
    if source.source_type == Source.SourceType.HTML:
        return scrape_html(source, url)
    if source.source_type == Source.SourceType.SELENIUM:
        return scrape_selenium(source, url, driver)
    raise ScraperError(f"Unsupported source type: {source.source_type}")


def polite_pause(source: Source):
    time.sleep(source.request_delay_seconds)
