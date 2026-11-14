# Deploying to a Hostinger VPS

`deploy/setup_vps.sh` installs everything: the website, HTTPS, Redis, and a Celery background worker that scans for leads every six hours, backs up daily and sends the daily summary email. It also removes the demo data, and does so again on every update.

## Before you start

- In hPanel, set the VPS operating system to **Ubuntu 24.04**.
- Point your domain's DNS **A record** at the VPS IP address, and wait for it to take effect. Without a domain the site works on plain HTTP only, which is fine for a quick test but not for real lead details.
- If the hPanel firewall is on, allow ports 22, 80 and 443.
- Have your Groq key ready.

## Install

1. On your Windows computer, in PowerShell, package and upload the project, replacing `VPS_IP`:

   ```powershell
   cd E:\mates
   tar --exclude=.venv --exclude=.deps --exclude=.git --exclude=.env --exclude=.env.example --exclude=db.sqlite3 --exclude=backups --exclude=staticfiles --exclude=__pycache__ --exclude=*.zip -czf ..\ndis.tar.gz .
   scp ..\ndis.tar.gz root@VPS_IP:/root/
   ```

2. Log in to the VPS and run the script, replacing the domain and email:

   ```bash
   ssh root@VPS_IP
   rm -rf /root/ndis && mkdir -p /root/ndis && tar -xzf /root/ndis.tar.gz -C /root/ndis
   cd /root/ndis
   bash deploy/setup_vps.sh your-domain.com you@example.com
   ```

   By passing an email, you accept the Let's Encrypt terms for the HTTPS certificate. The script asks for your API keys, which are hidden as you type, and for an administrator login.

3. Check it, with the keys loaded from the private settings file:

   ```bash
   cd /opt/ndis-lead-finder
   sudo -u ndis .venv/bin/python manage.py check_groq
   systemctl status ndis-worker.service
   journalctl -u ndis-worker.service -n 50
   ```

## Updating

Repeat steps 1 and 2. Your database, keys and backups are kept, and the database is backed up before it is migrated.

## Sources

The script approves Mastodon and Bluesky (Bluesky's public search needs no account), and asks about Brave if you enter a Brave key. Other sources stay off until you review and switch them on under **Settings → Scans & sources**. Owners and managers manage everything from **Settings**: sources, Scan now, search area and keywords, services and the team.

The script does not copy your local database, so the live site starts clean. After the first install, sign in and set **Settings → Search area & keywords → Ignore posts older than** to the window you want (30 days finds far more NDIS requests than the default 7).

## Email

The daily summary only sends once the `EMAIL_*` settings from `.env.example` are in `/opt/ndis-lead-finder/.env`. Until then, it writes to the log instead. Run `systemctl restart ndis-web.service` after changing that file.

## If something goes wrong

- **Site not loading:** `journalctl -u ndis-web.service -n 50`, then `nginx -t`.
- **No new leads:** `journalctl -u ndis-worker.service -n 50`, and check Redis with `redis-cli ping` (it should answer PONG). Also check "Source attention required" on the dashboard.
- **"Another scraper run appears active":** a run was interrupted. This clears by itself after three hours.
- **Restoring a backup:** stop the website with `systemctl stop ndis-web`, copy a file from `/opt/ndis-lead-finder/backups/` over `db.sqlite3`, run `chown ndis:ndis db.sqlite3`, then start it again.
