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
| Adzuna API | Very broad aggregator: councils, universities, developers, consultancies | free key |
| Reed API | UK's largest job board: lots of council, developer and consultancy roles | free key |
| DWP Find a Job, Guardian Jobs, LGJobs | Councils and the public sector | nothing |
| jobs.ac.uk | Universities | nothing |
| CharityJob, Environmentjob, CIEEM | Ecology and environment charities, consultancies | nothing |
| Careers pages | GLA, Royal Parks, Kew, Lee Valley, Canal & River Trust, London Wildlife Trust, Trees for Cities, Groundwork, Thames21, Cross River Partnership, London First, Grosvenor, British Land, Landsec, Berkeley, Argent, Lendlease | nothing |

No bot can read every employer's website, so the two APIs do most of the work:
nearly all councils, universities and developers re-post their jobs on those
boards. You can add any other organisation's jobs page in `config.yaml`. If a
site is down or has changed its layout, it's listed under "Sources that
couldn't be checked" and the rest of the run carries on.

`planning` only counts when it's in the **job title**, and titles such as
"financial planning" or "workforce planning" are excluded. Otherwise you'd get
thousands of unrelated jobs.

## Setup (about 15 minutes, free)
1. **Free API keys** (strongly recommended)
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
