# Deploying to Babal Host cPanel

This checklist targets a typical cPanel Python application. Exact menu names and Passenger support vary by plan; confirm them with Babal Host before production deployment.

## 1. Confirm host capabilities

Ask Babal Host to confirm:

- the available Python version (Python 3.11+ recommended);
- cPanel **Setup Python App** / Passenger support;
- SSH or terminal access;
- cron jobs and maximum execution time;
- outbound HTTPS access;
- whether headless Chrome/Chromium and ChromeDriver are permitted;
- backup limits and file retention.

If Chromium is unavailable, keep `SELENIUM_ENABLED=false`. RSS and HTML sources continue to work.

## 2. Upload and create the application

Upload the project outside `public_html` where possible, for example:

```text
/home/CPANEL_USER/ndis-lead-finder
```

In **Setup Python App**:

- Application root: `ndis-lead-finder`
- Application URL: your chosen domain/subdomain
- Startup file: `passenger_wsgi.py`
- Entry point: `application`

Install requirements inside the cPanel-created virtual environment:

```text
pip install -r requirements.txt
```

## 3. Add environment variables

Copy the names from `.env.example` into cPanel's environment-variable interface. Never upload a populated `.env` into a public directory. Generate a unique `DJANGO_SECRET_KEY` and `FIELD_ENCRYPTION_KEY`.

Set the absolute SQLite path and make sure the application user can write to both the database directory and backup directory. Do not place the database under `public_html`.

## 4. Initialise Django

Run:

```text
python manage.py migrate
python manage.py collectstatic --noinput
python manage.py createsuperuser
python manage.py seed_initial_data --username YOUR_ADMIN_USERNAME
python manage.py check --deploy
python manage.py check_gemini
```

`check_gemini` makes real API calls, so run it once `GEMINI_API_KEY` is set. If you use Groq instead, run `check_groq` once `GROQ_API_KEY` is set. Do not run `seed_demo_leads` on the live site.

Map `/static/` to the generated `staticfiles` directory using cPanel/Apache configuration. Configure uploaded media separately if a logo is used, and prevent script execution in that directory.

## 5. Configure cron

Use the full virtual-environment Python path displayed by cPanel. Example schedule:

```text
5 */6 * * * /path/to/venv/bin/python /home/CPANEL_USER/ndis-lead-finder/manage.py run_scrapers
20 2 * * * /path/to/venv/bin/python /home/CPANEL_USER/ndis-lead-finder/manage.py backup_database
30 8 * * * /path/to/venv/bin/python /home/CPANEL_USER/ndis-lead-finder/manage.py send_daily_summary
```

The scraper uses `.scraper.lock` to prevent overlapping SQLite writers. Do not run multiple scraper cron entries at the same time.

Shared hosting usually cannot run Redis or a Celery worker, so leave `REDIS_URL` empty here. Cron runs the jobs, and the **Scan now** button under Settings starts the same `run_scrapers` command as a separate process.

## 6. Security checklist

- `DJANGO_DEBUG=false`
- exact `DJANGO_ALLOWED_HOSTS`
- HTTPS active before secure-cookie/redirect settings are enabled
- `DJANGO_SECURE_COOKIES=true`
- `DJANGO_SECURE_SSL_REDIRECT=true`
- HSTS enabled only after HTTPS is proven stable
- strong admin password
- minimum staff permissions
- SMTP tested
- database and backup files outside the public web root
- source permissions reviewed before activation
- a tested restore procedure

## 7. Upgrade path

When concurrent staff activity or scraper volume causes SQLite contention:

1. Provision MySQL/MariaDB or PostgreSQL.
2. Pause scraper cron and take a backup.
3. Export/import the data using Django fixtures or a database migration tool.
4. Set `DB_ENGINE`, `DB_NAME`, `DB_USER`, `DB_PASSWORD`, `DB_HOST`, and `DB_PORT`.
5. Run migrations and smoke tests.
6. Re-enable cron.

If Selenium is important and shared hosting cannot run it, move the same project to a VPS and enable the Selenium setting after installing Chromium.
