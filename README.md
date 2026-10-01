# London green jobs – weekly web page bot

Every Monday it finds new London jobs matching your keywords and updates a web
page you can bookmark. The page groups jobs by councils, universities, Royal Parks,
developers, BIDs and charities. You can search it, filter by type, and switch
between this week's new jobs and the last 90 days.

## Setup (about 20 minutes, free)
1. **Free API keys**
   - Adzuna: https://developer.adzuna.com → sign up → copy App ID + App Key
   - Reed: https://www.reed.co.uk/developers → sign up → copy API key
2. **GitHub repo** – create a new repo and upload all these files, keeping the
   `.github` and `docs` folders. On a free GitHub account the repo must be
   *public* for Pages to work. Your API keys stay private (they go in Secrets),
   and the page is hidden from search engines.
3. **Secrets** – Settings → Secrets and variables → Actions → add
   `ADZUNA_APP_ID`, `ADZUNA_APP_KEY`, `REED_API_KEY`.
4. **Turn on the page** – Settings → Pages → Source: "Deploy from a branch",
   Branch: `main`, folder: `/docs` → Save.
5. **First run** – Actions tab → "Weekly London green jobs page" → Run workflow.
   A minute or two later the page is at `https://YOUR-USERNAME.github.io/REPO-NAME/`.

### Optional: email as well
Set `send_email: true` in config.yaml and add secrets `SMTP_HOST` (smtp.gmail.com),
`SMTP_PORT` (587), `SMTP_USER`, `SMTP_PASS` (a Gmail app password) and `EMAIL_TO`.

## Customising (config.yaml)
- Add/remove keywords, exclusions, radius, or employers in each category.
- Add RSS feeds from jobs.ac.uk, Guardian Jobs, CIEEM etc. (search London + keyword, copy the RSS link).
- Add any organisation's careers page under `career_pages`.

## Run locally instead
    pip install -r requirements.txt
    export ADZUNA_APP_ID=... ADZUNA_APP_KEY=... REED_API_KEY=...
    python jobbot.py      # builds docs/index.html – open it in a browser
