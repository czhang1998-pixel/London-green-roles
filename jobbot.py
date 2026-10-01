"""
London green-jobs weekly digest bot.
Sources: LinkedIn, Indeed, Guardian Jobs, optional Adzuna/Reed APIs, RSS feeds, job-board searches and careers pages.
Outputs: docs/index.html (web page), digest.md (weekly GitHub issue), optional email.
Settings live in config.yaml; secrets come from environment variables.
"""
import os, re, json, time, hashlib, smtplib, datetime, html
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

import requests, yaml, feedparser
from urllib.parse import quote_plus, urljoin
from bs4 import BeautifulSoup

HERE = os.path.dirname(os.path.abspath(__file__))
CFG = yaml.safe_load(open(os.path.join(HERE, "config.yaml"), encoding="utf-8"))
STATE_FILE = os.path.join(HERE, "state", "seen.json")
HISTORY_FILE = os.path.join(HERE, "state", "history.json")
SITE_DIR = os.path.join(HERE, "docs")
UA = {"User-Agent": "Mozilla/5.0 (London jobs digest bot)"}
BROWSER = {  # job boards serve simplified or blocked pages to bot-like agents
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/126.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-GB,en;q=0.9",
}
TODAY = datetime.date.today().isoformat()

KEYWORDS = CFG["keywords"]
TITLE_ONLY = [k.lower() for k in CFG.get("title_only_keywords", [])]
EXCLUDE = [e.lower() for e in CFG.get("exclude_phrases", [])]
errors = []


# ---------- matching & classification ----------
def find_keywords(title, body=""):
    """Return keywords matched. Title-only keywords must appear in the title."""
    hits = []
    for k in KEYWORDS:
        pat = r"\b" + re.escape(k) + r"\b"
        text = title if k.lower() in TITLE_ONLY else f"{title} {body}"
        if re.search(pat, text, re.I):
            hits.append(k)
    return hits


def excluded(title):
    t = title.lower()
    return any(e in t for e in EXCLUDE)


def categorise(employer, title=""):
    s = f"{employer} {title}".lower()
    for cat, words in CFG["categories"].items():
        if any(w.lower() in s for w in words):
            return cat
    return "Other"


def make_job(src, jid, title, employer, location, url, posted="", salary="", body=""):
    if excluded(title):
        return None
    kws = find_keywords(title, body)
    if not kws:
        return None
    return {
        "id": f"{src}:{jid}", "title": title.strip(), "employer": (employer or "").strip(),
        "location": location or "", "url": url, "posted": (posted or "")[:10],
        "salary": salary, "keywords": kws, "source": src,
        "category": categorise(employer or "", title),
    }


# ---------- sources ----------
def from_adzuna():
    app_id, app_key = os.getenv("ADZUNA_APP_ID"), os.getenv("ADZUNA_APP_KEY")
    if not (app_id and app_key):
        return []
    jobs = []
    for kw in KEYWORDS:
        try:
            r = requests.get(
                "https://api.adzuna.com/v1/api/jobs/gb/search/1",
                params={"app_id": app_id, "app_key": app_key, "what_phrase": kw,
                        "where": CFG["location"], "distance": CFG["radius_km"],
                        "max_days_old": CFG["lookback_days"], "results_per_page": 50,
                        "content-type": "application/json"},
                headers=UA, timeout=30)
            r.raise_for_status()
            for x in r.json().get("results", []):
                sal = ""
                if x.get("salary_min"):
                    sal = f"£{int(x['salary_min']):,}" + (f"–£{int(x['salary_max']):,}" if x.get("salary_max") else "")
                j = make_job("adzuna", x["id"], x.get("title", ""),
                             x.get("company", {}).get("display_name", ""),
                             x.get("location", {}).get("display_name", ""),
                             x.get("redirect_url"), x.get("created", ""), sal,
                             x.get("description", ""))
                if j: jobs.append(j)
        except Exception as e:
            errors.append(f"Adzuna ({kw}): {e}")
    return jobs


def from_reed():
    key = os.getenv("REED_API_KEY")
    if not key:
        return []
    jobs = []
    cutoff = datetime.date.today() - datetime.timedelta(days=CFG["lookback_days"])
    for kw in KEYWORDS:
        try:
            r = requests.get(
                "https://www.reed.co.uk/api/1.0/search",
                params={"keywords": kw, "locationName": CFG["location"],
                        "distanceFromLocation": int(CFG["radius_km"] * 0.62), "resultsToTake": 100},
                auth=(key, ""), headers=UA, timeout=30)
            r.raise_for_status()
            for x in r.json().get("results", []):
                try:  # Reed dates are dd/mm/yyyy
                    d = datetime.datetime.strptime(x.get("date", ""), "%d/%m/%Y").date()
                    if d < cutoff: continue
                    posted = d.isoformat()
                except ValueError:
                    posted = ""
                sal = ""
                if x.get("minimumSalary"):
                    sal = f"£{int(x['minimumSalary']):,}" + (f"–£{int(x['maximumSalary']):,}" if x.get("maximumSalary") else "")
                j = make_job("reed", x["jobId"], x.get("jobTitle", ""), x.get("employerName", ""),
                             x.get("locationName", ""), x.get("jobUrl"), posted, sal,
                             x.get("jobDescription", ""))
                if j: jobs.append(j)
        except Exception as e:
            errors.append(f"Reed ({kw}): {e}")
    return jobs


# ---------- job boards (LinkedIn, Indeed, Guardian Jobs) ----------
def _txt(el):
    return " ".join(el.get_text(" ").split()) if el else ""


def _board_get(url, params):
    r = requests.get(url, params=params, headers=BROWSER, timeout=30)
    if r.status_code in (403, 429, 999):
        raise RuntimeError(f"HTTP {r.status_code} – the site blocked the request (anti-bot protection)")
    r.raise_for_status()
    return r.text


def parse_linkedin(text):
    """Cards from LinkedIn's public (logged-out) job search."""
    soup, out, seen = BeautifulSoup(text, "html.parser"), [], set()
    for card in soup.select("div.base-search-card, div.base-card, li"):
        a = card.select_one("a.base-card__full-link, a[href*='/jobs/view/']")
        title = _txt(card.select_one(".base-search-card__title")) or _txt(a)
        if not (a and title):
            continue
        urn = card.get("data-entity-urn") or ""
        url = a["href"].split("?")[0]
        jid = urn.rsplit(":", 1)[-1] or re.sub(r"\D", "", url.rsplit("-", 1)[-1]) or url
        if jid in seen:
            continue
        seen.add(jid)
        t = card.select_one("time")
        out.append(dict(jid=jid, title=title, url=url,
                        employer=_txt(card.select_one(".base-search-card__subtitle")),
                        location=_txt(card.select_one(".job-search-card__location")),
                        posted=t.get("datetime", "") if t else ""))
    return out


def parse_indeed(text):
    """Indeed embeds results as JSON; fall back to the HTML cards."""
    out = []
    m = re.search(r'mosaic-provider-jobcards"\]\s*=\s*(\{.*?\});\s*\n', text, re.S)
    if m:
        try:
            data = json.loads(m.group(1))
            for x in data["metaData"]["mosaicProviderJobCardsModel"]["results"]:
                sal = (x.get("salarySnippet") or {}).get("text", "")
                posted = ""
                if x.get("pubDate"):
                    posted = datetime.date.fromtimestamp(x["pubDate"] / 1000).isoformat()
                out.append(dict(jid=x["jobkey"], title=x.get("displayTitle") or x.get("title", ""),
                                employer=x.get("company", ""), location=x.get("formattedLocation", ""),
                                url=f"https://uk.indeed.com/viewjob?jk={x['jobkey']}",
                                posted=posted, salary=sal, body=x.get("snippet", "")))
            return out
        except (KeyError, TypeError, ValueError):
            out = []
    soup = BeautifulSoup(text, "html.parser")
    for card in soup.select("div.job_seen_beacon, div.cardOutline, td.resultContent"):
        a = card.select_one("a[data-jk], h2.jobTitle a")
        if not a:
            continue
        jk = a.get("data-jk") or re.sub(r".*jk=([0-9a-f]+).*", r"\1", a.get("href", ""))
        out.append(dict(jid=jk, title=_txt(card.select_one("h2.jobTitle span[title]")) or _txt(a),
                        employer=_txt(card.select_one("[data-testid=company-name], .companyName")),
                        location=_txt(card.select_one("[data-testid=text-location], .companyLocation")),
                        url=f"https://uk.indeed.com/viewjob?jk={jk}",
                        salary=_txt(card.select_one("[data-testid*=salary], .salary-snippet-container"))))
    return out


def parse_guardian(text):
    """Guardian Jobs search results."""
    soup, out = BeautifulSoup(text, "html.parser"), []
    for card in soup.select("li.lister__item, article.lister__item, div.lister__item"):
        a = card.select_one(".lister__header a, h3 a, a[href*='/job/']")
        if not a:
            continue
        url = urljoin("https://jobs.theguardian.com/", a.get("href", ""))
        m = re.search(r"/job/(\d+)", url)
        out.append(dict(jid=m.group(1) if m else url, title=_txt(a), url=url,
                        employer=_txt(card.select_one(".lister__meta-item--recruiter")),
                        location=_txt(card.select_one(".lister__meta-item--location")),
                        salary=_txt(card.select_one(".lister__meta-item--salary")),
                        body=_txt(card.select_one(".lister__description"))))
    return out


def _search_linkedin(kw, cfg):
    jobs = []
    for start in range(0, cfg.get("pages", 3) * 25, 25):
        text = _board_get("https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search",
                          {"keywords": f'"{kw}"', "location": "London, England, United Kingdom",
                           "distance": int(CFG["radius_km"] * 0.62),
                           "f_TPR": f"r{CFG['lookback_days'] * 86400}", "start": start})
        batch = parse_linkedin(text)
        jobs += batch
        if len(batch) < 10:
            break
        time.sleep(cfg.get("delay_seconds", 3))
    return jobs


def _search_indeed(kw, cfg):
    jobs = []
    for start in range(0, cfg.get("pages", 2) * 10, 10):
        batch = parse_indeed(_board_get("https://uk.indeed.com/jobs",
                                        {"q": f'"{kw}"', "l": "London", "radius": int(CFG["radius_km"] * 0.62),
                                         "fromage": CFG["lookback_days"], "start": start}))
        jobs += batch
        if len(batch) < 10:
            break
        time.sleep(cfg.get("delay_seconds", 3))
    return jobs


def _search_guardian(kw, cfg):
    jobs = []
    for page in range(1, cfg.get("pages", 2) + 1):
        batch = parse_guardian(_board_get("https://jobs.theguardian.com/jobs/london/",
                                          {"keywords": kw, "radialtown": "London", "page": page}))
        jobs += batch
        if len(batch) < 20:
            break
        time.sleep(cfg.get("delay_seconds", 3))
    return jobs


BOARDS = {"linkedin": ("LinkedIn", _search_linkedin),
          "indeed": ("Indeed", _search_indeed),
          "guardian": ("Guardian Jobs", _search_guardian)}


def from_boards():
    jobs = []
    for key, cfg in (CFG.get("job_boards") or {}).items():
        cfg = cfg or {}
        if key not in BOARDS or not cfg.get("enabled", True):
            continue
        name, search = BOARDS[key]
        found = 0
        for kw in KEYWORDS:
            try:
                for x in search(kw, cfg):
                    j = make_job(key, x["jid"], x["title"], x.get("employer", ""), x.get("location", ""),
                                 x["url"], x.get("posted", ""), x.get("salary", ""), x.get("body", ""))
                    if j:
                        j["source"] = name
                        jobs.append(j); found += 1
            except Exception as e:
                errors.append(f"{name} ({kw}): {e}")
                if "blocked" in str(e):
                    break  # no point trying the other keywords
            time.sleep(cfg.get("delay_seconds", 3))
        print(f"{name}: {found} matching listings")
    return jobs


def from_rss():
    jobs = []
    for feed in CFG.get("rss_feeds") or []:
        try:
            f = feedparser.parse(feed["url"], agent=UA["User-Agent"])
            if f.bozo and not f.entries:
                raise ValueError("feed unreadable")
            for e in f.entries:
                text = f"{e.get('title','')} {e.get('summary','')}"
                if feed.get("require_london", True) and "london" not in text.lower():
                    continue
                jid = e.get("id") or e.get("link")
                j = make_job("rss", hashlib.md5(jid.encode()).hexdigest(), e.get("title", ""),
                             feed.get("employer", feed["name"]), "London", e.get("link"),
                             e.get("published", ""), "", e.get("summary", ""))
                if j:
                    j["source"] = feed["name"]
                    jobs.append(j)
        except Exception as ex:
            errors.append(f"RSS {feed['name']}: {ex}")
    return jobs


def _expand_pages():
    """Pages whose URL contains {kw} are fetched once per keyword (search pages)."""
    for page in CFG.get("career_pages") or []:
        if "{kw}" in page["url"]:
            for kw in KEYWORDS:
                yield {**page, "url": page["url"].replace("{kw}", quote_plus(kw)), "_kw": kw}
        else:
            yield page


def _is_facet(title):
    """Skip search-filter links like 'Biodiversity (12)' or a bare keyword."""
    t = re.sub(r"[\s(]*\d[\d,]*\)?\s*$", "", title).strip().lower()
    return t in {k.lower() for k in KEYWORDS} or t.startswith(("search", "sort by", "filter", "jobs in"))


def scan_page(page, text):
    soup = BeautifulSoup(text, "html.parser")
    jobs = []
    for el in soup.find_all(["a", "h2", "h3", "h4"]):
        title = " ".join(el.get_text(" ").split())
        if not (4 < len(title) < 150) or _is_facet(title):
            continue
        href = el.get("href") if el.name == "a" else None
        if href and href.startswith(("javascript:", "mailto:", "#")):
            continue
        url = urljoin(page["url"], href) if href else page["url"]
        j = make_job("page", hashlib.md5(f"{url}|{title}".encode()).hexdigest(),
                     title, page["name"], "London", url)
        if j:
            if page.get("category"):
                j["category"] = page["category"]
            j["source"] = page["name"]
            jobs.append(j)
    return jobs


def from_pages():
    """Scan careers pages and job-board search pages for keyword links/headings."""
    jobs, failed = [], set()
    for page in _expand_pages():
        if page["name"] in failed:
            continue
        try:
            r = requests.get(page["url"], headers=UA, timeout=30)
            r.raise_for_status()
            jobs += scan_page(page, r.text)
        except Exception as ex:
            failed.add(page["name"])
            errors.append(f"Page {page['name']}: {ex}")
    return jobs


# ---------- state ----------
def load_seen():
    try:
        return json.load(open(STATE_FILE))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_seen(seen):
    cutoff = (datetime.date.today() - datetime.timedelta(days=120)).isoformat()
    seen = {k: v for k, v in seen.items() if v >= cutoff}
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    json.dump(seen, open(STATE_FILE, "w"), indent=0, sort_keys=True)


def dedupe(jobs):
    """Same title + employer from different sources counts once."""
    out, keys = [], set()
    for j in jobs:
        k = (re.sub(r"\W", "", j["title"].lower()), re.sub(r"\W", "", j["employer"].lower()))
        if k not in keys:
            keys.add(k); out.append(j)
    return out


# ---------- web page ----------
def update_history(new):
    try:
        hist = json.load(open(HISTORY_FILE))
    except (FileNotFoundError, json.JSONDecodeError):
        hist = []
    for j in new:
        hist.append({**j, "first_seen": TODAY})
    keep = CFG.get("site_history_days", 90)
    cutoff = (datetime.date.today() - datetime.timedelta(days=keep)).isoformat()
    hist = [j for j in hist if j["first_seen"] >= cutoff]
    json.dump(hist, open(HISTORY_FILE, "w"), indent=0)
    return hist


def build_site(hist, n_new):
    tpl = open(os.path.join(HERE, "site_template.html"), encoding="utf-8").read()
    data = {"updated": TODAY, "new_count": n_new, "jobs": hist, "errors": errors,
            "categories": list(CFG["categories"].keys()) + ["Other"]}
    blob = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    os.makedirs(SITE_DIR, exist_ok=True)
    open(os.path.join(SITE_DIR, "index.html"), "w", encoding="utf-8").write(tpl.replace("__DATA__", blob))
    open(os.path.join(SITE_DIR, ".nojekyll"), "w").close()


# ---------- digest ----------
def build_html(jobs):
    order = list(CFG["categories"].keys()) + ["Other"]
    parts = [f"<h2>London green jobs – week of {TODAY}</h2>",
             f"<p>{len(jobs)} new listing(s) matching: {', '.join(KEYWORDS)}</p>"]
    for cat in order:
        group = [j for j in jobs if j["category"] == cat]
        if not group: continue
        parts.append(f"<h3>{html.escape(cat)} ({len(group)})</h3><ul>")
        for j in sorted(group, key=lambda x: x["title"]):
            meta = " · ".join(filter(None, [j["employer"], j["location"], j["salary"], j["posted"]]))
            parts.append(
                f'<li><a href="{html.escape(j["url"] or "")}"><b>{html.escape(j["title"])}</b></a><br>'
                f'<small>{html.escape(meta)}<br>Matched: {", ".join(j["keywords"])} · via {j["source"]}</small></li>')
        parts.append("</ul>")
    if not jobs:
        parts.append("<p>No new matching jobs this week.</p>")
    if errors:
        parts.append("<hr><p><small><b>Sources that couldn't be checked:</b><br>"
                     + "<br>".join(html.escape(e) for e in errors) + "</small></p>")
    return "\n".join(parts)


def build_markdown(jobs):
    """Digest posted as a weekly GitHub Issue (GitHub emails you about it)."""
    esc = lambda t: re.sub(r"([\[\]|*_`<>])", r"\\\1", t or "")
    page = os.getenv("SITE_URL", "")
    out = [f"**{len(jobs)} new London job(s)** matching: {', '.join(KEYWORDS)}"]
    if page:
        out.append(f"\nFull searchable list (last {CFG.get('site_history_days', 90)} days): {page}")
    order = list(CFG["categories"].keys()) + ["Other"]
    for cat in order:
        group = sorted((j for j in jobs if j["category"] == cat), key=lambda x: x["title"])
        if not group: continue
        out.append(f"\n### {cat} ({len(group)})")
        for j in group:
            meta = " · ".join(filter(None, [j["employer"], j["location"], j["salary"], j["posted"]]))
            out.append(f"- [{esc(j['title'])}]({j['url']}) — {esc(meta)}  \n  <sub>matched: {', '.join(j['keywords'])} · via {esc(j['source'])}</sub>")
    if not jobs:
        out.append("\nNo new matching jobs this week.")
    if errors:
        out.append("\n<details><summary>Sources that couldn't be checked this week (" + str(len(errors)) + ")</summary>\n")
        out += [f"- {esc(e)[:300]}" for e in errors]
        out.append("\n</details>")
    return "\n".join(out)[:60000]


def send_email(body_html, n):
    host, to = os.getenv("SMTP_HOST"), os.getenv("EMAIL_TO")
    if not (host and to):
        print("No SMTP settings – skipping email (digest.md still written).")
        return
    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"London green jobs: {n} new ({TODAY})"
    msg["From"] = os.getenv("SMTP_USER")
    msg["To"] = to
    msg.attach(MIMEText(body_html, "html"))
    with smtplib.SMTP(host, int(os.getenv("SMTP_PORT", 587))) as s:
        s.starttls()
        s.login(os.getenv("SMTP_USER"), os.getenv("SMTP_PASS"))
        s.sendmail(msg["From"], [a.strip() for a in to.split(",")], msg.as_string())


def main():
    seen = load_seen()
    all_jobs = dedupe(from_boards() + from_adzuna() + from_reed() + from_rss() + from_pages())
    new = [j for j in all_jobs if j["id"] not in seen]
    for j in new:
        seen[j["id"]] = TODAY
    build_site(update_history(new), len(new))
    if CFG.get("send_email", False):
        body = build_html(new)
        if new or CFG.get("email_when_empty", True):
            send_email(body, len(new))
    open(os.path.join(HERE, "digest.md"), "w", encoding="utf-8").write(build_markdown(new))
    save_seen(seen)
    print(f"{len(new)} new jobs; {len(errors)} source errors.")


if __name__ == "__main__":
    main()
