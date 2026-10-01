# London green jobs – weekly bot

Every Monday at 07:00 UTC this bot looks for **new London jobs** matching
`ecology manager`, `biodiversity`, `green space` or `planning`, then:

1. **Opens a GitHub Issue** with that week's new jobs, grouped by councils,
   universities, Royal Parks, developers, BIDs/business collectives and
   charities. GitHub emails you about it, so you get a weekly update without
   setting up email.
2. **Updates a web page** you can bookmark. It lists the last 90 days of jobs,
   with search, a type filter, and a "new this week" switch.
3. Optionally **sends an HTML email** through your own SMTP account.

## Where it looks
| Source | Covers | Needs |
|---|---|---|
| **LinkedIn** | Developers, consultancies, BIDs, universities, councils | nothing |
| **Indeed** | The biggest UK aggregator: councils, universities, developers | nothing |
| **Guardian Jobs** | Councils, the public sector, charities, universities | nothing |
| DWP Find a Job, LGJobs | Councils and the public sector | nothing |
| jobs.ac.uk | Universities | nothing |
| CharityJob, Environmentjob, CIEEM | Ecology and environment charities, consultancies | nothing |
| Careers pages | GLA, Royal Parks, Kew, Lee Valley, Canal & River Trust, London Wildlife Trust, Trees for Cities, Groundwork, Thames21, Cross River Partnership, London First, Grosvenor, British Land, Landsec, Berkeley, Argent, Lendlease | nothing |
| Adzuna / Reed APIs | Optional extras: used only if you add their free keys | free key |

**About scraping big job sites.** LinkedIn and Indeed don't offer a public
feed, so the bot reads their public search results pages, the same pages you
see when you search without logging in. Be aware that:
- Their terms of use forbid automated scraping. One small search a week for
  personal use is low-key, but it's your call. Switch either off with
  `enabled: false` under `job_boards` in `config.yaml`.
- They block traffic from cloud servers. **Indeed in particular often blocks
  GitHub's servers**, and when that happens the weekly issue says
  "HTTP 403 – the site blocked the request". If it keeps happening, the free
  Adzuna and Reed keys are the reliable fallback, because Adzuna carries most
  Indeed listings anyway.
- If a site changes its layout, that source returns nothing until the scraper
  is updated.

`planning` only counts when it's in the **job title**, and titles such as
"financial planning" or "workforce planning" are excluded. Otherwise you'd get
thousands of unrelated jobs.

## Setup (about 15 minutes, free)
1. **Free API keys** (optional extra coverage – skip if you like)
   - Adzuna: https://developer.adzuna.com → sign up → copy the App ID and App Key
   - Reed: https://www.reed.co.uk/developers → sign up → copy the API key
2. **Secrets**: in the repo, go to Settings → Secrets and variables → Actions →
   New repository secret, and add `ADZUNA_APP_ID`, `ADZUNA_APP_KEY` and `REED_API_KEY`.
3. **Web page** (optional): go to Settings → Pages → "Deploy from a branch" →
   branch `main`, folder `/docs` → Save. The page will be at
   `https://YOUR-USERNAME.github.io/REPO-NAME/`. On a free account the repo
   must be public, but your keys stay private in Secrets.
4. **Workflow branch**: scheduled runs only happen from the default branch
   (`main`), so merge these files into `main`.
5. **First run**: go to the Actions tab → "Weekly London green jobs page" →
   Run workflow. After a couple of minutes a new issue appears under Issues.
6. Make sure you're "Watching" the repo (the default for your own repos) with
   email notifications on, so the weekly issue reaches your inbox.

### Optional: email as well
Set `send_email: true` in `config.yaml` and add these secrets: `SMTP_HOST`
(e.g. smtp.gmail.com), `SMTP_PORT` (587), `SMTP_USER`, `SMTP_PASS` (a Gmail
app password) and `EMAIL_TO` (comma-separate several addresses).

## Customising (`config.yaml`)
- Add or remove keywords and exclusions, or change the radius or look-back period.
- `categories`: the words used to sort employers into groups.
- `career_pages`: add any organisation's jobs page. If a URL contains `{kw}`,
  it's searched once for each keyword.
- `rss_feeds`: paste any job RSS feed (for example, a London university's
  vacancy feed).

## Run locally
    pip install -r requirements.txt
    export ADZUNA_APP_ID=... ADZUNA_APP_KEY=... REED_API_KEY=...
    python jobbot.py      # writes docs/index.html and digest.md

Files: `jobbot.py` (the bot), `config.yaml` (settings), `site_template.html`
(the web page design), `state/` (jobs already reported, so you never see a
job twice), `docs/` (the published page).
