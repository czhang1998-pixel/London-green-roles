"""
London green-jobs weekly digest bot.
Sources: Adzuna API, Reed API, RSS feeds, job-board searches and careers pages.
Outputs: docs/index.html (web page), digest.md (weekly GitHub issue), optional email.
Settings live in config.yaml; secrets come from environment variables.
"""
import os, re, json, hashlib, smtplib, datetime, html
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
        errors.append("Adzuna skipped: add ADZUNA_APP_ID and ADZUNA_APP_KEY secrets for much wider coverage")
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
        errors.append("Reed skipped: add the REED_API_KEY secret for much wider coverage")
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
    all_jobs = dedupe(from_adzuna() + from_reed() + from_rss() + from_pages())
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
