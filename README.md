# Website Monitor

Configuration-driven monitoring for small and medium websites. One YAML profile per site describes what "healthy" means; the same engine runs it locally, in CI and on a schedule.

It also tests the site's **forms end to end without sending a single email**. The [Forms](#testing-forms-without-sending-mail) section explains how.

`config/sites/example.yaml` is the documented template. Real site profiles live in the git-ignored `config/private/` folder (see [Public repositories](#public-repositories)).

## Checks

| Check | What it verifies |
|---|---|
| `uptime` | Status code, time to first byte, unexpected redirects, forbidden final URLs (e.g. a countdown/maintenance page) |
| `ssl` | Certificate trust, hostname, expiry (warn/fail days), TLS version, for the main host and extra hosts |
| `redirects` | HTTP→HTTPS, alias domains → canonical host, legacy URL rules, redirect chains |
| `links` | Crawls internal pages; broken links/resources, **soft 404s** (HTTP 200 + fallback page), missing `#anchors` |
| `assets` | Same-origin CSS/JS/images/fonts (incl. CSS `url()`) load with the right MIME type |
| `headers` | HSTS, CSP, nosniff, clickjacking, Referrer/Permissions/COOP policies, version leaks, cookie flags, with fix hints |
| `seo` | Title/description, canonical, h1, lang, viewport, Open Graph, duplicates, robots.txt, sitemap URLs, soft 404 |
| `content` | Required phrases per page; forbidden text (Lorem ipsum, PHP errors, stack traces) |
| `accessibility` | Static HTML heuristics: labels, alt, accessible names, duplicate ids, heading order, zoom |
| `exposure` | `.git`, `.env`, configs, directory listings and similar sensitive paths are not readable |
| `performance` | TTFB, HTML size, gzip/brotli, long-term caching of fingerprinted assets |
| `form_endpoint` | Live, **mail-free** probes of the form backend's guards (method, Origin, honeypot, time-trap) |
| `browser` | Chromium per page × viewport: JS exceptions, failed requests, console errors, `undefined`/`NaN` in rendered text, overflow, broken images, load/transfer budgets, screenshots |
| `forms` | Fill and submit forms in Chromium: structure, validation, payload, success/error UI, honeypot round-trip |
| `interactions` | Scripted journeys: click/fill/press → expect visible/text/attribute/URL (menus, toggles, navigation) |
| `axe` | axe-core WCAG 2.1 A/AA audit of the rendered DOM |

`python -m monitor list-checks --site <profile>` shows which checks a profile enables. Each check's options are documented in the docstring at the top of `monitor/checks/<check>.py`.

## Setup

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
python -m playwright install chromium     # only needed for browser/forms/interactions/axe
```

## Usage

```bash
python -m monitor run --site mysite                  # every enabled check
python -m monitor run --site mysite --suite quick    # suites are defined in the profile
python -m monitor run --site mysite --checks forms,interactions --headed -v
python -m monitor run --site mysite --base-url http://localhost:5173   # same profile, other deployment
python -m monitor run --site mysite --fail-on warning                  # stricter exit code
python -m monitor validate                                 # check all profiles offline
python -m monitor list-sites
```

Each run writes `reports/<profile>/<UTC timestamp>/` containing `report.html` (self-contained, with screenshots), `report.md`, `report.json` and `junit.xml`. It also updates `reports/<profile>/latest.json`.

Exit codes: `0` ok, `1` failures (or warnings with `--fail-on warning`), `2` configuration error.

Statuses:
- **fail** means broken for users, e.g. down, a JS crash, or a form that cannot be submitted.
- **warning** means worth fixing, e.g. missing security headers, a slow page, or SEO issues.
- **error** means the check itself crashed.

## Testing forms without sending mail

Several independent layers keep the monitor from ever filling a mailbox:

1. **`safety.block_urls`.** Every browser page aborts requests to these URLs. A smoke test that clicks around can therefore never post to the mailer.
2. **`forms` mock scenarios.** The endpoint is intercepted inside Chromium, and the monitor answers it itself. The browser still runs the site's real JavaScript, so the check can verify:
   - the request it builds: method, every field, the honeypot left empty, the time-trap timestamp;
   - the success UI it shows;
   - the error UI for a server error (`mock_error`) and for a network failure (`mock_network_error`);
   - that empty, incomplete or invalid input sends nothing (`validation`).
3. **`forms` `live_honeypot` scenario.** This round-trip does reach the real backend, but the route only lets the request through if its payload has the honeypot field filled. A filled honeypot means the server answers "ok" and drops the message.
   - `live_payload_overrides` also rewrites the payload, for example with a fresh time-trap timestamp and an unknown form type. If a later change ever removed the honeypot guard, the next guard would still reject the request. The test reports the regression instead of sending mail.
4. **`form_endpoint` probes.** Plain HTTP requests that each stop at one server guard: wrong method, foreign Origin, honeypot, or time-trap.
   - Config validation **refuses any POST probe without a `safe_because`** explanation.
   - Probes are chosen to stay ahead of the rate limiter, so they don't use up real visitors' quota.

The test suite checks these properties against a fake mailer that records every message it would have delivered (`tests/fixture_server.py`). Each test asserts that it recorded none, including when the server's honeypot guard and time-trap are both broken.

## Adding a site

1. Copy `config/sites/example.yaml` to `config/private/<name>.yaml` (git-ignored). Use `config/sites/` only for profiles you are happy to publish.
2. Set `base_url`, `paths` and the checks you want. Values like `${MY_URL:-https://…}` read environment variables.
3. Run `python -m monitor validate <name>`, then `python -m monitor run --site <name> -v`.

The engine needs no changes for ordinary sites. To add a new check, create `monitor/checks/<name>.py` with `run(ctx, options) -> list[CheckResult]` and register it in `monitor/checks/__init__.py`.

## Being a polite client

Shared hosts often ban IPs that send bursts of requests, and a ban looks exactly like an outage. The monitor keeps its footprint small:
- It spaces requests apart (`request_delay_ms`).
- It downloads each URL once per run and shares the result between checks.
- It retries only idempotent requests.
- After a few consecutive connection failures it stops contacting that host. A connect-timeout message says the monitor's IP may be rate-limited.

## Scheduled monitoring (GitHub Actions)

- `.github/workflows/ci.yml` runs lint, profile validation and tests on every push. It only uses the fixture site.
- `.github/workflows/monitor.yml` runs the `quick` suite every 15 minutes and everything hourly. You can also run it manually and pick a suite.

Set up two repository secrets (Settings → Secrets and variables → Actions):

| Secret | Content |
|---|---|
| `MONITOR_PROFILE` | The full YAML of the site's profile, e.g. the contents of `config/private/<name>.yaml` |
| `REPORT_PASSPHRASE` | A long random passphrase; the full report is uploaded only encrypted with it |

To read a report, download the artifact, unzip it, then decrypt:

```bash
openssl enc -d -aes-256-cbc -pbkdf2 -iter 200000 -in monitor-report.tar.gz.enc | tar xz
open reports/site/*/report.html
```

**Email alerts.** When a run finds warnings or failures, the workflow opens a GitHub issue labelled `monitor-<suite>`, and GitHub emails you as the repository owner at your account's notification address. No address is stored anywhere.
- A comment, and so an email, is added only when the set of problems changes.
- The issue closes itself, with a final email, once everything passes again.
- The issue shows only the verdict and counts.
- The job itself fails only when the monitor could not run, which triggers GitHub's normal failed-workflow email.

Make sure you watch the repository and have email notifications enabled under GitHub → Settings → Notifications. GitHub pauses scheduled workflows after 60 days without repository activity; re-enable them on the Actions tab.

## Public repositories

Logs, job summaries and artifacts of public repositories are visible to everyone, and a monitoring report is a list of a site's weak spots. The project therefore keeps everything site-specific out of the repository and out of the public output:

- **Profiles:** real profiles live in `config/private/` (git-ignored) locally and in the `MONITOR_PROFILE` secret in Actions. They describe endpoints, spam guards and sensitive paths.
- **`--redact` (`MONITOR_REDACT=1`):** prints only the verdict and counts. It hides the site, URLs, check targets, findings and configuration errors, and it is also applied to the job summary.
- **Reports:** reports include screenshots and response details, so the workflow uploads them only encrypted.

## Development

```bash
ruff check .
python -m pytest -q           # browser tests skip automatically without Chromium
python -m tests.fixture_server   # serve the fixture site + fake mailer on :8765
```
