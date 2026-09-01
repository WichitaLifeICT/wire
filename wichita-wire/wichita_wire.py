#!/usr/bin/env python3
"""
Wichita Wire — pull every local news feed into one page you can actually skim.

Usage:
    python3 wichita_wire.py                 # fetch everything, write wichita-wire.html
    python3 wichita_wire.py --days 3        # only stories from the last 3 days
    python3 wichita_wire.py --discover      # re-sniff each outlet for its feed URL
    python3 wichita_wire.py --out ~/wire.html

No pip installs. Standard library only.
Edit SOURCES below to add or drop outlets.
"""

import argparse
import difflib
import gzip
import html as htmllib
import io
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from xml.etree import ElementTree as ET

UA = "Mozilla/5.0 (compatible; WichitaWire/1.0; +local newsletter research)"
TIMEOUT = 20

# ---------------------------------------------------------------------------
# Sources. "feeds" are tried in order; the first one that parses wins.
# "home" is used by --discover to sniff the real feed URL out of the page HTML.
# ---------------------------------------------------------------------------
SOURCES = [
    {
        "name": "KWCH 12",
        "home": "https://www.kwch.com/",
        "feeds": ["https://www.kwch.com/arc/outboundfeeds/rss/?outputType=xml"],
    },
    {
        "name": "KAKE",
        "home": "https://www.kake.com/",
        "feeds": ["https://www.kake.com/rss/", "https://www.kake.com/feed/"],
    },
    {
        "name": "KSN 3",
        "home": "https://www.ksn.com/",
        "feeds": ["https://www.ksn.com/feed/", "https://www.ksn.com/news/feed/"],
    },
    {
        "name": "Wichita Eagle",
        "home": "https://www.kansas.com/",
        "feeds": ["https://www.kansas.com/news/local/?widgetName=rssfeed&getXmlFeed=true"],
    },
    {
        "name": "KMUW",
        "home": "https://www.kmuw.org/",
        "feeds": ["https://www.kmuw.org/index.rss", "https://www.kmuw.org/rss.xml"],
    },
    {
        "name": "Wichita Beacon",
        "home": "https://thebeaconnews.org/wichita/",
        "feeds": ["https://thebeaconnews.org/wichita/feed/", "https://thebeaconnews.org/feed/"],
    },
    {
        "name": "Wichita Business Journal",
        "home": "https://www.bizjournals.com/wichita/",
        "feeds": ["https://www.bizjournals.com/wichita/news/rss.xml"],
    },
    {
        "name": "Wichita State",
        "home": "https://www.wichita.edu/about/wsunews/",
        "feeds": ["https://www.wichita.edu/about/wsunews/news.rss"],
    },
    {
        "name": "Google News: Wichita",
        "home": None,
        "feeds": [
            "https://news.google.com/rss/search?q=Wichita+OR+%22Sedgwick+County%22"
            "&hl=en-US&gl=US&ceid=US:en"
        ],
    },
]

CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "wire_feeds.json")


# ---------------------------------------------------------------------------
# Fetching
# ---------------------------------------------------------------------------
def get(url):
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": UA,
            "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml, text/html;q=0.8",
            "Accept-Encoding": "gzip",
        },
    )
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        raw = resp.read()
        if resp.headers.get("Content-Encoding") == "gzip":
            raw = gzip.GzipFile(fileobj=io.BytesIO(raw)).read()
        charset = resp.headers.get_content_charset() or "utf-8"
    return raw.decode(charset, errors="replace")


class FeedSniffer(HTMLParser):
    """Pulls <link rel=alternate type=application/rss+xml> out of a homepage."""

    def __init__(self):
        super().__init__()
        self.found = []

    def handle_starttag(self, tag, attrs):
        if tag != "link":
            return
        a = dict(attrs)
        rel = (a.get("rel") or "").lower()
        typ = (a.get("type") or "").lower()
        if "alternate" in rel and ("rss" in typ or "atom" in typ) and a.get("href"):
            self.found.append((a.get("title") or "", a["href"]))


def discover(source):
    """Sniff a homepage for its declared feeds. Returns list of absolute URLs."""
    if not source.get("home"):
        return []
    try:
        page = get(source["home"])
    except Exception as e:
        print(f"  ! {source['name']}: could not load homepage ({e})", file=sys.stderr)
        return []
    sniffer = FeedSniffer()
    try:
        sniffer.feed(page)
    except Exception:
        pass
    return [urllib.parse.urljoin(source["home"], href) for _, href in sniffer.found]


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------
NS = {
    "atom": "http://www.w3.org/2005/Atom",
    "media": "http://search.yahoo.com/mrss/",
    "content": "http://purl.org/rss/1.0/modules/content/",
    "dc": "http://purl.org/dc/elements/1.1/",
}

TAG_RE = re.compile(r"<[^>]+>")
WS_RE = re.compile(r"\s+")


def clean(text, limit=280):
    if not text:
        return ""
    text = TAG_RE.sub(" ", text)
    text = htmllib.unescape(text)
    text = WS_RE.sub(" ", text).strip()
    if len(text) > limit:
        text = text[:limit].rsplit(" ", 1)[0] + "…"
    return text


def parse_date(value):
    if not value:
        return None
    value = value.strip()
    try:
        dt = parsedate_to_datetime(value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        pass
    iso = value.replace("Z", "+00:00")
    for candidate in (iso, iso[:19], iso[:10]):
        try:
            dt = datetime.fromisoformat(candidate)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except Exception:
            continue
    return None


def first_text(node, *paths):
    for path in paths:
        found = node.find(path, NS)
        if found is not None and (found.text or "").strip():
            return found.text.strip()
    return ""


def find_image(node):
    for path in ("media:thumbnail", "media:content"):
        el = node.find(path, NS)
        if el is not None and el.get("url"):
            return el.get("url")
    enc = node.find("enclosure")
    if enc is not None and (enc.get("type") or "").startswith("image"):
        return enc.get("url")
    body = first_text(node, "content:encoded", "description")
    if body:
        m = re.search(r'<img[^>]+src=["\']([^"\']+)', body)
        if m:
            return m.group(1)
    return None


def parse_feed(xml_text, source_name):
    root = ET.fromstring(xml_text.lstrip())
    items = []

    # RSS 2.0
    for node in root.findall(".//item"):
        link = first_text(node, "link", "guid")
        title = clean(first_text(node, "title"), 220)
        if not link or not title:
            continue
        items.append(
            {
                "title": title,
                "link": link.strip(),
                "summary": clean(first_text(node, "description", "content:encoded")),
                "published": parse_date(first_text(node, "pubDate", "dc:date")),
                "image": find_image(node),
                "source": source_name,
            }
        )

    # Atom
    for node in root.findall(".//atom:entry", NS):
        link = ""
        for el in node.findall("atom:link", NS):
            if el.get("rel") in (None, "alternate") and el.get("href"):
                link = el.get("href")
                break
        title = clean(first_text(node, "atom:title"), 220)
        if not link or not title:
            continue
        items.append(
            {
                "title": title,
                "link": link.strip(),
                "summary": clean(first_text(node, "atom:summary", "atom:content")),
                "published": parse_date(first_text(node, "atom:published", "atom:updated")),
                "image": find_image(node),
                "source": source_name,
            }
        )

    return items


# ---------------------------------------------------------------------------
# Normalising and de-duping
# ---------------------------------------------------------------------------
JUNK_PARAMS = ("utm_", "fbclid", "gclid", "mc_cid", "mc_eid", "ito", "oc=")


def canonical(url):
    try:
        parts = urllib.parse.urlsplit(url)
    except Exception:
        return url
    keep = [
        (k, v)
        for k, v in urllib.parse.parse_qsl(parts.query, keep_blank_values=True)
        if not any(k.startswith(j.rstrip("=")) for j in JUNK_PARAMS)
    ]
    host = parts.netloc.lower().removeprefix("www.")
    path = parts.path.rstrip("/")
    return urllib.parse.urlunsplit(("https", host, path, urllib.parse.urlencode(keep), ""))


def title_key(title):
    t = title.lower()
    t = re.sub(r"[^a-z0-9 ]+", " ", t)
    t = WS_RE.sub(" ", t).strip()
    # Google News appends " - Outlet Name"
    return t


def dedupe(items):
    seen_urls = {}
    kept = []
    for item in items:
        url = canonical(item["link"])
        if url in seen_urls:
            merge(seen_urls[url], item)
            continue
        key = title_key(item["title"])
        match = None
        for existing in kept:
            if difflib.SequenceMatcher(None, key, existing["_key"]).ratio() > 0.90:
                match = existing
                break
        if match:
            merge(match, item)
            continue
        item["_key"] = key
        item["_url"] = url
        seen_urls[url] = item
        kept.append(item)
    return kept


def merge(keeper, dupe):
    """Second sighting of a story: record the extra outlet, keep the better data."""
    others = keeper.setdefault("also", [])
    if dupe["source"] != keeper["source"] and dupe["source"] not in others:
        others.append(dupe["source"])
    if not keeper.get("image") and dupe.get("image"):
        keeper["image"] = dupe["image"]
    if len(dupe.get("summary") or "") > len(keeper.get("summary") or ""):
        keeper["summary"] = dupe["summary"]
    if dupe.get("published") and (
        not keeper.get("published") or dupe["published"] < keeper["published"]
    ):
        keeper["published"] = dupe["published"]


# ---------------------------------------------------------------------------
# Collection
# ---------------------------------------------------------------------------
def load_feed_map():
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH) as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def save_feed_map(feed_map):
    with open(CONFIG_PATH, "w") as f:
        json.dump(feed_map, f, indent=2)


def collect(days, do_discover, keep_undated=False):
    feed_map = load_feed_map()
    items = []
    report = []

    for source in SOURCES:
        name = source["name"]
        candidates = []
        if feed_map.get(name):
            candidates.append(feed_map[name])
        candidates += [u for u in source["feeds"] if u not in candidates]
        if do_discover:
            print(f"  sniffing {name}…")
            for url in discover(source):
                if url not in candidates:
                    candidates.append(url)

        got = None
        for url in candidates:
            try:
                parsed = parse_feed(get(url), name)
            except Exception:
                continue
            if parsed:
                got = (url, parsed)
                break

        if got:
            url, parsed = got
            feed_map[name] = url
            items.extend(parsed)
            report.append({"source": name, "ok": True, "count": len(parsed), "feed": url})
            print(f"  ✓ {name}: {len(parsed)} stories")
        else:
            report.append({"source": name, "ok": False, "count": 0, "feed": None})
            print(f"  ✗ {name}: no working feed. Try --discover, or edit SOURCES.", file=sys.stderr)

    save_feed_map(feed_map)

    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    before = len(items)
    if keep_undated:
        items = [i for i in items if not i["published"] or i["published"] >= cutoff]
    else:
        # Undated entries are where the ancient stuff hides. Drop them unless asked.
        items = [i for i in items if i["published"] and i["published"] >= cutoff]
    dropped = before - len(items)
    if dropped:
        print(f"  dropped {dropped} stories older than {days}d or with no date")
    items = dedupe(items)
    items.sort(key=lambda i: i["published"] or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
    return items, report


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------
def build_html(items, report, days):
    here = os.path.dirname(os.path.abspath(__file__))
    with open(os.path.join(here, "wire_template.html")) as f:
        template = f.read()

    payload = []
    for i in items:
        payload.append(
            {
                "title": i["title"],
                "link": i["link"],
                "summary": i.get("summary", ""),
                "source": i["source"],
                "also": i.get("also", []),
                "image": i.get("image"),
                "published": i["published"].astimezone(timezone.utc).isoformat()
                if i.get("published")
                else None,
            }
        )

    data = {
        "stories": payload,
        "report": report,
        "days": days,
        "generated": datetime.now(timezone.utc).isoformat(),
    }
    blob = json.dumps(data).replace("</", "<\\/")  # keep any HTML in summaries from closing the script tag
    return template.replace("__WIRE_DATA__", blob)


def main():
    ap = argparse.ArgumentParser(description="Pull Wichita news feeds into one skimmable page.")
    ap.add_argument("--days", type=int, default=3, help="how far back to look (default 2)")
    ap.add_argument("--discover", action="store_true", help="re-sniff each outlet for its feed URL")
    ap.add_argument("--out", default="wichita-wire.html", help="output file")
    ap.add_argument("--json", metavar="FILE", help="also write the stories as JSON")
    ap.add_argument("--keep-undated", action="store_true",
                    help="keep stories with no publish date (off by default — that is where old links hide)")
    args = ap.parse_args()

    print(f"Pulling the last {args.days} day(s)…")
    items, report = collect(args.days, args.discover, args.keep_undated)
    if not items:
        print("\nNothing came back. Run again with --discover to re-sniff feed URLs.", file=sys.stderr)
        return 1

    out = os.path.abspath(os.path.expanduser(args.out))
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w") as f:
        f.write(build_html(items, report, args.days))

    if args.json:
        jpath = os.path.abspath(os.path.expanduser(args.json))
        os.makedirs(os.path.dirname(jpath), exist_ok=True)
        with open(jpath, "w") as f:
            json.dump(
                [
                    {
                        "title": i["title"],
                        "link": i["link"],
                        "summary": i.get("summary", ""),
                        "source": i["source"],
                        "also": i.get("also", []),
                        "published": i["published"].astimezone(timezone.utc).isoformat()
                        if i.get("published")
                        else None,
                    }
                    for i in items
                ],
                f,
                indent=2,
            )
        print(f"stories JSON → {jpath}")

    working = sum(1 for r in report if r["ok"])
    print(f"\n{len(items)} stories from {working}/{len(report)} feeds → {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
