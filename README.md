# NDIS Lead Finder

NDIS Lead Finder is a Django-based internal lead-intelligence and CRM application for Australian disability and home-care businesses. It discovers **publicly posted service requests** from sources that have been reviewed and approved for crawling. It does not identify anonymous search-engine users and does not bypass authentication, CAPTCHAs, robots rules, or access controls.

## Included in the MVP

C:\Users\shwet\OneDrive\Documents\ChatGPT\mates- Custom responsive staff dashboard using Django templates.
- Django Unfold administration for sources, keywords, locations, users, and system records.
- RSS/Atom, configurable HTML, and optional Selenium source adapters.
- Per-source permission review gates, crawl delay, run logs, and failure visibility.
- Rule-based intent classification with Gemini relevance scoring, a live AI search page, and optional OpenAI-compatible AI enrichment using an encrypted bring-your-own key.
- Australian service taxonomy, locations, freshness rules, false-positive filtering, and duplicate detection.
- Lead claim protection, CRM statuses, notes, reminders, audit records, notifications, reports, and CSV export.
- SQLite MVP with sequential scraper locking and a documented future database migration path.
- Daily backups, summary email command, automated tests, and cPanel deployment files.

## What it deliberately does not do

- It cannot reveal who privately searched Google or another directory.
- It does not scrape private Facebook groups, private profiles, private messages, or login-protected content.
- It does not send automatic marketing emails, SMS, calls, or WhatsApp messages.
- It does not activate a source until its terms and robots policy have been reviewed.
- It does not copy full pages. It stores a short relevant snippet and source URL.

## Local setup on Windows

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item .env.example .env
```

Edit `.env` with your settings. The app loads `.env` from the project folder at startup, and real environment variables take priority over it. Keep keys in `.env`, which Git ignores, and never in `.env.example`. Restart the server after changing it. Because the example file holds production values, on your own computer also set `DJANGO_DEBUG=true`, `DJANGO_SECURE_COOKIES=false`, `DJANGO_SECURE_SSL_REDIRECT=false`, `DJANGO_ALLOWED_HOSTS=127.0.0.1,localhost` and remove the `DB_NAME` line.

```powershell
python manage.py migrate
python manage.py createsuperuser
python manage.py seed_initial_data --username admin
python manage.py seed_demo_leads
python manage.py runserver
```

The dashboard stays empty until a source is reviewed, approved and run (see **Safe source setup**). `seed_demo_leads` loads ten clearly fictional leads through the real classifier so you can try the CRM straight away. Remove them with `python manage.py seed_demo_leads --clear`.

Open:

- Staff application: `http://127.0.0.1:8000/`
- Unfold administration: `http://127.0.0.1:8000/admin/`

## Safe source setup

1. Open **Administration → Sources → Add source**.
2. Record the exact source domain and search/feed URL template.
3. Review the site's terms, robots policy, API/feed options, and permitted request rate.
4. Select the correct source type.
5. For HTML/Selenium, provide selectors such as:

```json
{
  "item": ".search-result",
  "title": "h2",
  "link": "h2 a",
  "snippet": ".description",
  "date": "time",
  "name": ".author",
  "phone": ".phone",
  "email": ".email"
}
```

6. Mark `robots reviewed`, `terms reviewed`, and `review status = Approved` only after review.
7. Enable the source and test it with:

```powershell
python manage.py run_scrapers --source SOURCE_ID --profile PROFILE_ID
```

The search URL template supports `{query}`, `{location}`, and `{page}`. It must remain on the approved base domain.

## Free API sources

`seed_initial_data` adds three API sources, disabled and pending review. API sources follow the provider's API terms instead of robots.txt. They only keep posts that contain service-seeking language and one of your service keywords.

| Source | Key needed | Notes |
|---|---|---|
| Brave web search | `BRAVE_API_KEY` | Best coverage: finds public forum and Reddit threads. The monthly free credit covers roughly 1,000 searches, so keep queries few and avoid `{location}`. |
| Bluesky post search | `BLUESKY_HANDLE`, `BLUESKY_APP_PASSWORD` | Free with any account and an app password. Low NDIS volume. |
| Mastodon public hashtags | none | Works immediately. Very low volume of real requests. |

Edit the hashtags or queries in each source's configuration JSON. A query containing `{location}` runs once per search-profile location. A query containing `{service}` runs once per offered service (SIL, STA, daily living, community participation, life skills, transport, support coordination, personal care, cleaning, OT, behaviour support, therapy assistant); add a `"services"` list to the configuration to change them. Bluesky uses `{service}`; Brave groups the services with `OR` to stay inside its free credit. Then approve and enable the source as described above.

To replace the queries of API sources created by an earlier seed, run `python manage.py seed_initial_data --refresh-sources`.

Not supported: crawling Reddit directly (its robots.txt disallows all crawlers, and commercial use of its API needs a paid agreement), and Google's Custom Search API (closed to new customers).

## Scheduled commands

Run every six hours:

```text
python manage.py run_scrapers
```

Run daily:

```text
python manage.py backup_database
python manage.py send_daily_summary
```

## Selenium

Selenium is disabled by default. Set `SELENIUM_ENABLED=true` only after confirming that the host provides a compatible Chrome/Chromium binary and permits headless browser processes. The scraper uses explicit waits and always quits the driver.

## Gemini AI

Set `GEMINI_API_KEY`, then run `python manage.py check_gemini` to confirm that both features work. Use a Google project with billing enabled: Search grounding is not available on the free tier, and Google may use free-tier content to improve its products and have people review it, which is not suitable for posts that can describe real people.

Gemini does two separate jobs:

- **Scoring (automatic).** Every new post found by your sources is read by Gemini, which sets its label, service, location and a 0-100 relevance score. Posts scoring below `GEMINI_MIN_RELEVANCE` cannot be marked as likely. If a call fails, the rule-based classifier's result is kept and the lead shows no AI score.
- **AI search (on request).** The **AI search** page runs a live Google-grounded search for the staff member who asks.

AI search results are deliberately not saved as leads. The Gemini API terms allow grounded results to be shown only to the person who submitted the prompt, with Google's Search Suggestions, and do not allow caching them, analysing them or collecting their links. Do not schedule `search_for_leads` or store its output.

## Groq

To use an open-source model on Groq, set `GROQ_API_KEY` from [console.groq.com/keys](https://console.groq.com/keys) and run `python manage.py check_groq`. When it is set, Groq handles both scoring and the AI search page, even if Gemini is also set. Empty it to go back to Gemini.

In Groq's Data Controls settings, turn on Zero Data Retention, and check Groq's Services Agreement for how it treats your data before scoring real posts. Groq rate-limits requests, so a large batch can be slow. A post that still cannot be scored keeps its rule-based result and shows no AI score. One AI search uses far more tokens than scoring a post, so searches eat into the daily limit quickly.

AI search only shows links that Groq's search really returned. Any other link in the answer is removed.

## One-week freshness

`Freshness days` on the search profile defaults to 7. Posts older than that are not added as leads. A post with no date can only be marked as needs review. The dashboard shows only dated likely leads inside the window, best AI score first, and the Leads page defaults to the window plus anything already claimed. Choose **Posted: Any time** to see older leads.

## Other AI providers

If `GEMINI_API_KEY` is empty, an OpenAI-compatible service can be used instead:

1. Generate and set `FIELD_ENCRYPTION_KEY`.
2. Add an AI configuration in Unfold admin.
3. Enter the base URL, model, and API key.
4. Enable it.

If the AI call fails, the deterministic classifier remains active. AI output cannot initiate outreach or infer diagnosis, eligibility, funding, or sensitive personal information.

## Testing and production checks

```powershell
python manage.py test
python manage.py check
python manage.py check --deploy
```

Production warnings about HSTS/SSL are expected until the real domain and TLS settings are applied. See [DEPLOYMENT_HOSTINGER.md](DEPLOYMENT_HOSTINGER.md) for a Hostinger VPS, or [DEPLOYMENT_BABAL.md](DEPLOYMENT_BABAL.md) for cPanel.
#   l e a d  
 