#!/usr/bin/env python3
"""Merge the latest RSS items into a rolling archive and regenerate the viewers.

Writes:
  docs/items.json  - the archive (read by the GitHub Pages viewer)
  README.md        - a scrollable list rendered directly on the repo's front page
"""
import calendar
import html
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import feedparser

FEED_URL = os.environ.get("FEED_URL", "https://rss.app/feeds/_R7sHm4k7q4AJzH9o.xml")
RETENTION_DAYS = int(os.environ.get("RETENTION_DAYS", "30"))
PAGES_URL = os.environ.get("PAGES_URL", "").strip()
LOCAL_TZ = ZoneInfo(os.environ.get("DISPLAY_TZ", "America/New_York"))

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "docs" / "items.json"
README = ROOT / "README.md"


def clean_text(s, limit=300):
    s = re.sub(r"<[^>]+>", " ", s or "")
    s = html.unescape(s)
    s = re.sub(r"\s+", " ", s).strip()
    if len(s) <= limit:
        return s
    return s[:limit].rsplit(" ", 1)[0] + "…"


def find_image(e):
    for key in ("media_content", "media_thumbnail"):
        for m in e.get(key) or []:
            url = m.get("url")
            if url and m.get("medium") in (None, "image"):
                return url
    for enc in e.get("enclosures") or []:
        if (enc.get("type") or "").startswith("image") and enc.get("href"):
            return enc["href"]
    raw = e.get("summary") or ""
    m = re.search(r'<img[^>]+src=["\']([^"\']+)', raw)
    return html.unescape(m.group(1)) if m else None


def entry_date(e):
    t = e.get("published_parsed") or e.get("updated_parsed")
    if not t:
        return None
    return datetime.fromtimestamp(calendar.timegm(t), timezone.utc).isoformat()


def parse_dt(s):
    return datetime.fromisoformat(s)


def md_escape(s):
    return re.sub(r"([\[\]*_`<>|\\])", r"\\\1", s)


def main():
    now = datetime.now(timezone.utc)

    previous = []
    if DATA.exists():
        previous = json.loads(DATA.read_text()).get("items", [])
    archive = {it["id"]: it for it in previous}

    feed = feedparser.parse(FEED_URL, agent="feed-archive/1.0 (GitHub Actions)")
    if not feed.entries:
        print(f"No entries fetched (bozo={feed.bozo}: {feed.get('bozo_exception')})", file=sys.stderr)
        sys.exit(1)

    new = 0
    for e in feed.entries:
        link = e.get("link")
        # rss.app guids are timestamps, so prefer the link as the stable key
        item_id = link or e.get("id") or e.get("title")
        if not item_id:
            continue
        summary = clean_text(e.get("summary") or e.get("description"))
        title = clean_text(e.get("title"), 200) or summary[:120] or link
        # Social posts often have a title that's just the summary truncated
        stem = title.rstrip(".…").strip()
        if summary == title or (len(stem) > 20 and summary.startswith(stem)):
            title, summary = summary if len(summary) <= 200 else title, \
                ("" if len(summary) <= 200 else summary)
        item = {
            "id": item_id,
            "title": title,
            "link": link,
            "summary": summary,
            "author": clean_text(e.get("author"), 80),
            "image": find_image(e),
            "published": entry_date(e),
        }
        if item_id in archive:
            item["first_seen"] = archive[item_id]["first_seen"]
        else:
            item["first_seen"] = now.isoformat()
            new += 1
        archive[item_id] = item

    cutoff = now - timedelta(days=RETENTION_DAYS)
    kept = []
    for it in archive.values():
        it["date"] = it.get("published") or it["first_seen"]
        if parse_dt(it["date"]) >= cutoff:
            kept.append(it)
    kept.sort(key=lambda it: it["date"], reverse=True)

    if kept == previous:
        print("No changes.")
        return

    DATA.parent.mkdir(parents=True, exist_ok=True)
    DATA.write_text(json.dumps({
        "feed_title": clean_text(feed.feed.get("title"), 200),
        "feed_link": feed.feed.get("link"),
        "updated": now.isoformat(),
        "retention_days": RETENTION_DAYS,
        "items": kept,
    }, indent=1, ensure_ascii=False))

    write_readme(kept, feed, now)
    print(f"{new} new, {len(kept)} kept, {len(archive) - len(kept)} pruned.")


def write_readme(items, feed, now):
    title = clean_text(feed.feed.get("title"), 200) or "Feed archive"
    stamp = now.astimezone(LOCAL_TZ).strftime("%b %-d, %Y %-I:%M %p %Z")
    lines = [f"# {md_escape(title)}", ""]
    lines.append(f"Last {RETENTION_DAYS} days · {len(items)} items · updated {stamp}")
    if PAGES_URL:
        lines.append(f"  \n**[Open the searchable viewer →]({PAGES_URL})**")
    lines.append("")

    current_day = None
    for it in items:
        local = parse_dt(it["date"]).astimezone(LOCAL_TZ)
        day = local.strftime("%A, %B %-d")
        if day != current_day:
            lines += ["", f"## {day}", ""]
            current_day = day
        meta = " · ".join(x for x in (md_escape(it["author"]) if it.get("author") else "",
                                      local.strftime("%-I:%M %p")) if x)
        t = md_escape(it["title"])
        head = f"**[{t}]({it['link']})**" if it.get("link") else f"**{t}**"
        lines.append(f"- {head} <sub>{meta}</sub>")
        if it.get("summary"):
            lines.append(f"  <br>{md_escape(it['summary'])}")
    lines.append("")
    README.write_text("\n".join(lines))


if __name__ == "__main__":
    main()
