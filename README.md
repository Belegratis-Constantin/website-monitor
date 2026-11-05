# Website Monitor

A modular, configuration-driven monitoring framework. CAP.finisherclub is the first profile; add other websites by creating another YAML file in `config/sites/`.

## Included checks

- `uptime`: HTTP status, redirects and response time.
- `links`: internal links, linked resources and in-page anchors.
- `assets`: same-origin CSS, JavaScript, image and linked assets.
- `headers`: recommended HTTP security headers.
- `seo`: title, description, canonical, H1, language, Open Graph and image-alt basics.
- `content`: expected text on configured routes.
- `ssl`: TLS certificate validity and expiry.
- `accessibility`: basic HTML checks for document language, image alternatives and form labels.
- `browser`: Chromium desktop/mobile smoke tests, JS exceptions, failed same-origin requests, horizontal overflow, member form presence and language switch smoke test.

These are smoke checks, not a guarantee of complete correctness. The accessibility module is not a full WCAG audit. The member form is checked without submitting data. External links are not crawled by default.

## Local setup

```bash
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\\Scripts\\activate
pip install -r requirements.txt
playwright install chromium
python -m monitor list-checks
python -m monitor run --site cap-finisher
```

Run selected modules:

```bash
python -m monitor run --site cap-finisher --checks uptime,links,seo
```

Reports are written to `reports/<site>/<timestamp>/report.md` and `report.json`.

## Add another site

Copy `config/sites/example.yaml`, rename it and update URL, paths, enabled modules and expectations. Run it with `python -m monitor run --site <profile-name>`. The engine does not need to change for ordinary sites.

## CAP.finisherclub profile

The profile targets `https://cap-finisher.com` and these public routes:

- `/`
- `/mitglieder.html`
- `/partner.html`
- `/events.html`
- `/team.html`
- `/impressum.html`
- `/datenschutz.html`
- `/accessibility.html`

It checks the member form's email/select fields without submitting it and smoke-tests the language switch. If the site's markup or labels change, update `config/sites/cap-finisher.yaml`.

## GitHub Actions

HTTP-oriented checks run every five minutes and browser checks twice per hour. Scheduled GitHub Actions can be delayed. Reports are retained as workflow artifacts for 30 days. A failed check fails its job.

Keep credentials in GitHub Actions Secrets, never in the public repository. Standard GitHub-hosted runner usage for public repositories is subject to GitHub's current terms and limits.
