#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Company Names from Job URLs (Windows-Ready, No API Keys) — v3
--------------------------------------------------------------
- Input: URLs via --urls ... or --urls-file input.txt (one per line, any count).
- Output: CSV of ONLY company names (single column by default).
- Supports: Indeed search & job pages, LinkedIn search lists & job pages,
            and local saved HTML files (to bypass LinkedIn login wall).
- Auto-detects LinkedIn search pages and returns ONLY the left results list,
  so counts match the visible “N results”.

Typical usage (PowerShell/CMD):
  py -3 main.py --urls-file input.txt --out company_names.csv

Optional:
  py -3 main.py --urls-file input.txt --report page_report.csv
  py -3 main.py --urls-file input.txt --keep-duplicates
  py -3 main.py --urls "https://www.indeed.com/jobs?...“ "C:\\path\\saved_linkedin.html"

"""

import argparse
import csv
import os
import re
import sys
import time
from typing import Iterable, List, Set, Optional, Tuple
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


# ---------------- HTTP session (robust on Windows) ----------------
def make_session() -> requests.Session:
    sess = requests.Session()
    retries = Retry(
        total=3,
        backoff_factor=0.7,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset(['GET'])
    )
    adapter = HTTPAdapter(max_retries=retries, pool_connections=20, pool_maxsize=20)
    sess.mount("http://", adapter)
    sess.mount("https://", adapter)
    sess.headers.update({
        "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                       "AppleWebKit/537.36 (KHTML, like Gecko) "
                       "Chrome/120.0.0.0 Safari/537.36"),
        "Accept-Language": "en-US,en;q=0.9",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Connection": "keep-alive"
    })
    return sess


# ---------------- I/O helpers ----------------
def read_urls(urls: List[str], urls_file: Optional[str]) -> List[str]:
    collected: List[str] = []
    if urls:
        collected.extend(urls)
    if urls_file and os.path.exists(urls_file):
        with open(urls_file, "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                s = line.strip()
                if s:
                    collected.append(s)

    out: List[str] = []
    seen: Set[str] = set()
    for u in collected:
        u = u.strip().strip('"').strip("'")
        if not u:
            continue
        if "://" not in u and os.path.exists(u):
            norm = os.path.abspath(u)
            key = f"file:{norm.lower()}"
        else:
            norm = u
            key = u.lower()
        if key not in seen:
            seen.add(key)
            out.append(norm)
    return out


def is_local_path(u: str) -> bool:
    if u.lower().startswith("file://"):
        return True
    if "://" not in u and os.path.exists(u):
        return True
    return False


def fetch(u: str, sess: requests.Session, timeout: int = 25) -> str:
    if is_local_path(u):
        path = u.replace("file://", "")
        try:
            with open(path, "r", encoding="utf-8", errors="ignore") as f:
                return f.read()
        except Exception as e:
            sys.stderr.write(f"[WARN] Could not read local file: {u} ({e})\n")
            return ""
    try:
        r = sess.get(u, timeout=timeout)
        r.raise_for_status()
        return r.text
    except Exception as e:
        sys.stderr.write(f"[WARN] HTTP fetch failed: {u} ({e})\n")
        return ""


def domain(u: str) -> str:
    try:
        d = urlparse(u).netloc.lower()
        return d[4:] if d.startswith("www.") else d
    except Exception:
        return ""


# ---------------- Normalization & noise filtering ----------------
def clean_company_name(s: str) -> str:
    s = re.sub(r"\s+", " ", s).strip(" -|\n\t\r")
    s = re.sub(r"\s*\|\s*(LinkedIn|Indeed|Glassdoor|Monster).*?$", "", s, flags=re.I)
    return s.strip()


def looks_like_noise(name: str) -> bool:
    """
    Filter obvious non-company noise like:
      - '208 EDM Operator AND ... jobs in United States'
      - anything that literally contains 'jobs in'
      - generic 'apply/hiring/careers' tokens without brand context
    """
    n = name.strip()
    if len(n) < 2:
        return True
    low = n.lower()
    if "jobs in" in low:
        return True
    if re.search(r"\b\d+\b.*\bjobs?\b", low):
        return True
    if re.search(r"\b(apply|hiring|careers?)\b", low) and not re.search(r"[A-Za-z]{3,}\s", low):
        return True
    return False


def dedupe_preserve_order(items: Iterable[str]) -> List[str]:
    seen: Set[str] = set()
    out: List[str] = []
    for x in items:
        k = x.lower().strip()
        if not k or k in seen:
            continue
        seen.add(k)
        out.append(x.strip())
    return out


# ---------------- Indeed extractor ----------------
def extract_from_indeed(html: str) -> Tuple[List[str], str]:
    soup = BeautifulSoup(html, "lxml")
    names: List[str] = []

    # Search listing cards (several current/legacy patterns)
    for card in soup.select("[data-jk], .job_seen_beacon, .resultContent, .tapItem, .slider_container"):
        c1 = card.select_one(".companyName")
        if c1 and c1.get_text(strip=True):
            names.append(clean_company_name(c1.get_text(strip=True)))
            continue
        for sel in [
            "[data-testid='company-name']",
            ".companyInfo span",
            ".companyInfo a",
            ".company_location .companyName"
        ]:
            el = card.select_one(sel)
            if el and el.get_text(strip=True):
                names.append(clean_company_name(el.get_text(strip=True)))
                break

    # Individual job page containers (various layouts)
    for sel in [
        ".jobsearch-CompanyInfoContainer a[data-tn-element='companyName']",
        "#companyInfo a",
        "[data-company-name]",
        ".icl-u-lg-mr--sm.icl-u-xs-mr--xs",
        ".jobsearch-CompanyInfoWithoutHeaderImage div a",
        ".jobsearch-InlineCompanyRating div"
    ]:
        for el in soup.select(sel):
            txt = el.get_text(strip=True)
            if txt:
                names.append(clean_company_name(txt))

    # Fallback via <title>
    title = soup.title.get_text(strip=True) if soup.title else ""
    if title:
        parts = [p.strip() for p in title.split(" - ") if p.strip()]
        if len(parts) >= 2:
            names.append(clean_company_name(parts[1]))

    # Finalize
    names = [n for n in names if n and not looks_like_noise(n)]
    return dedupe_preserve_order(names), "indeed"


# ---------------- LinkedIn extractor ----------------
def linkedin_jobs_list_names(soup: BeautifulSoup) -> List[str]:
    names: List[str] = []
    # Primary: modern cards under the results list
    for li in soup.select("ul.jobs-search__results-list li"):
        # anchor in subtitle
        a = li.select_one("h4.base-search-card__subtitle a, .base-search-card__subtitle a")
        if a and a.get_text(strip=True):
            names.append(clean_company_name(a.get_text(strip=True)))
            continue
        # subtitle without anchor
        h4 = li.select_one("h4.base-search-card__subtitle")
        if h4 and h4.get_text(strip=True):
            names.append(clean_company_name(h4.get_text(strip=True)))
            continue
        # older variants
        alt = li.select_one(".job-card-container__primary-description, .job-card-container__company-name")
        if alt and alt.get_text(strip=True):
            names.append(clean_company_name(alt.get_text(strip=True)))
            continue
    # drop obvious noise
    return [n for n in names if n and n.lower() != "linkedin" and not looks_like_noise(n)]


def is_linkedin_search_page(url: str, html: str) -> bool:
    if "linkedin.com" not in domain(url):
        return False
    if "/jobs/search" in url:
        return True
    # heuristic for saved HTML
    if re.search(r'jobs-search__results-list', html, re.I):
        return True
    return False


def extract_from_linkedin(html: str, url: str) -> Tuple[List[str], str]:
    soup = BeautifulSoup(html, "lxml")

    # If it's a search page, return ONLY the left results list (to match N results)
    if is_linkedin_search_page(url, html):
        list_names = linkedin_jobs_list_names(soup)
        if list_names:
            return dedupe_preserve_order(list_names), "linkedin_list"
        # if list empty (blocked), continue to fallbacks below

    names: List[str] = []

    # Try list anyway (if not detected, but present)
    for n in linkedin_jobs_list_names(soup):
        names.append(n)

    # Detail page company names (topcard etc.)
    for sel in [
        "a.topcard__org-name-link",
        "a.topcard__flavor",
        "a[href*='/company/']",
        "span.topcard__flavor"
    ]:
        for el in soup.select(sel):
            txt = el.get_text(strip=True)
            if txt and not re.search(r"sign in|join now", txt, re.I):
                names.append(clean_company_name(txt))

    # Meta tags sometimes like "Job Title - Company | LinkedIn"
    for meta_name in ["og:title", "twitter:title"]:
        m = soup.find("meta", attrs={"property": meta_name}) or soup.find("meta", attrs={"name": meta_name})
        if m and m.get("content"):
            content = clean_company_name(m["content"])
            parts = [p.strip() for p in content.split(" - ") if p.strip()]
            if len(parts) >= 2:
                names.append(clean_company_name(parts[1]))

    # Fallback via <title>
    title = soup.title.get_text(strip=True) if soup.title else ""
    if title:
        parts = [p.strip() for p in title.split(" - ") if p.strip()]
        if len(parts) >= 2:
            names.append(clean_company_name(parts[1]))

    names = [n for n in names if n and n.lower() != "linkedin" and not looks_like_noise(n)]
    return dedupe_preserve_order(names), "linkedin_fallback"


# ---------------- Generic fallback ----------------
def extract_generic(html: str) -> Tuple[List[str], str]:
    soup = BeautifulSoup(html, "lxml")
    names: Set[str] = set()
    title = soup.title.get_text(strip=True) if soup.title else ""
    if title:
        parts = [p.strip() for p in re.split(r"[-|•–—]", title) if p.strip()]
        for p in parts:
            if not re.search(r"(job|jobs|apply|hiring|careers?)", p, re.I):
                names.add(clean_company_name(p))
    names = [n for n in names if not looks_like_noise(n)]
    return dedupe_preserve_order(list(names)), "generic"


# ---------------- Main extraction dispatcher ----------------
def extract_company_names(url: str, html: str) -> Tuple[List[str], str]:
    d = domain(url)
    if "indeed" in d:
        return extract_from_indeed(html)
    if "linkedin" in d:
        return extract_from_linkedin(html, url)
    if is_local_path(url):
        # if saved HTML, try to guess which engine
        if re.search(r"indeed", html, re.I):
            return extract_from_indeed(html)
        if re.search(r"linkedin", html, re.I):
            return extract_from_linkedin(html, url)
    return extract_generic(html)


# ---------------- CLI ----------------
def main():
    p = argparse.ArgumentParser(description="Extract ONLY company names from LinkedIn/Indeed job URLs (Windows-ready) — v3")
    p.add_argument("--urls", nargs="*", help="One or more URLs or file paths.")
    p.add_argument("--urls-file", default="input.txt", help="Text file with one URL per line (default: input.txt).")
    p.add_argument("--out", default="company_names.csv", help="Output CSV (single column 'company_name' by default).")
    p.add_argument("--report", help="Optional per-URL report CSV (url, domain, extractor, names_count, note).")
    p.add_argument("--sleep", type=float, default=1.0, help="Seconds to sleep between requests (polite).")
    p.add_argument("--keep-duplicates", action="store_true", help="Keep duplicates (default removes dups).")
    p.add_argument("--source-column", action="store_true", help="Include 'source' and 'from_url' columns in the output.")
    args = p.parse_args()

    targets = read_urls(args.urls or [], args.urls_file)
    if not targets:
        sys.stderr.write("No URLs found. Put them in input.txt or pass with --urls ...\n")
        sys.exit(1)

    sess = make_session()
    all_names: List[str] = []
    enriched_rows: List[Tuple[str, str, str]] = []  # (name, source, from_url)
    report_rows: List[Tuple[str, str, str, int, str]] = []  # (url, domain, extractor, count, note)

    for u in targets:
        print(f"[INFO] Fetching: {u}")
        html = fetch(u, sess=sess)
        if not html:
            report_rows.append((u, domain(u), "fetch_error", 0, "empty_html"))
            continue

        names, extractor = extract_company_names(u, html)
        if not args.keep_duplicates:
            names = dedupe_preserve_order(names)

        if names:
            print(f"  -> {len(names)} names from this page via {extractor}.")
        else:
            note = "linkedin_login_wall?" if "linkedin" in domain(u) else "no_names_detected"
            print(f"  [WARN] No names found ({note}).")
            report_rows.append((u, domain(u), extractor, 0, note))

        for n in names:
            if args.source_column:
                enriched_rows.append((n, extractor.split("_")[0], u))
            else:
                all_names.append(n)

        if names:
            report_rows.append((u, domain(u), extractor, len(names), "ok"))

        time.sleep(args.sleep)

    # Final de-dup across all pages (unless user kept duplicates)
    if not args.keep_duplicates:
        if args.source_column:
            # de-dup by company name only; keep first source/url
            seen = set()
            deduped = []
            for n, src, url in enriched_rows:
                k = n.lower().strip()
                if k in seen:
                    continue
                seen.add(k)
                deduped.append((n, src, url))
            enriched_rows = deduped
        else:
            all_names = dedupe_preserve_order(all_names)

    # Write main output
    with open(args.out, "w", newline="", encoding="utf-8") as f:
        if args.source_column:
            w = csv.writer(f)
            w.writerow(["company_name", "source", "from_url"])
            for n, src, url in enriched_rows:
                w.writerow([n, src, url])
            print(f"[DONE] Wrote {len(enriched_rows)} rows to {args.out}")
        else:
            w = csv.writer(f)
            w.writerow(["company_name"])
            for n in all_names:
                w.writerow([n])
            print(f"[DONE] Wrote {len(all_names)} names to {args.out}")

    # Optional per-URL report
    if args.report:
        with open(args.report, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["url", "domain", "extractor", "names_count", "note"])
            for row in report_rows:
                w.writerow(list(row))
        print(f"[INFO] Wrote per-URL report to {args.report}")


if __name__ == "__main__":
    main()
