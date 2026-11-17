#!/usr/bin/env python3
"""Render digest-*.md files to RSS 2.0 XML (stdlib only)."""
import argparse
import os
import re
import sys
from datetime import datetime, timezone
from email.utils import format_datetime

DATE_RE = re.compile(r"digest-(\d{4})-(\d{2})-(\d{2})\.md$")
BARE_LINK_RE = re.compile(r"^\s*[-*]\s+(https?://\S+)\s*$")

def esc(s):
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

def inline(s):
    s = esc(s)
    s = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", s)
    return s

def md_to_html(text):
    lines = text.split("\n")
    out = []
    para = []

    def flush():
        if para:
            out.append("<p>" + inline(" ".join(para)) + "</p>")
            para.clear()

    for raw in lines:
        s = raw.rstrip()
        if s.startswith("## "):
            flush()
            out.append("<h3>" + inline(s[3:].strip()) + "</h3>")
        elif s.strip() == "---":
            flush()
            out.append("<hr/>")
        elif BARE_LINK_RE.match(s):
            flush()
            url = BARE_LINK_RE.match(s).group(1)
            out.append('<p><a href="%s">%s</a></p>' % (esc(url), esc(url)))
        elif s.strip() == "":
            flush()
        else:
            t = s.strip()
            if t.startswith("- "):
                t = t[2:]
            para.append(t)
    flush()
    return "\n".join(out)

def parse_date(path):
    m = DATE_RE.search(os.path.basename(path))
    if m:
        y, mo, d = map(int, m.groups())
        try:
            return datetime(y, mo, d, tzinfo=timezone.utc)
        except ValueError:
            pass
    return None

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("digest", help="path to a digest-YYYY-MM-DD.md file")
    ap.add_argument("--out", help="write XML to file instead of stdout")
    ap.add_argument("--feed-url", required=True, help="absolute feed URL for <link>")
    ap.add_argument(
        "--item-link",
        default="https://gist.github.com/agaruppa/8a96021ae2e6ceeefe3232e51a0f68e8",
        help="absolute URL for item <link> (readers drop items without one)",
    )
    args = ap.parse_args()

    path = os.path.abspath(args.digest)
    d = parse_date(path)
    if d is None:
        d = datetime.now(timezone.utc)

    with open(path, encoding="utf-8") as f:
        text = f.read()

    # Drop the H1 line; item title already carries the date.
    lines = text.split("\n")
    body_lines = lines[1:] if lines and lines[0].startswith("# ") else lines
    current_html = md_to_html("\n".join(body_lines))

    # Collect up to 3 newest digests from the same directory.
    dirc = os.path.dirname(path)
    cands = []
    for fn in os.listdir(dirc):
        if DATE_RE.search(fn):
            fp = os.path.join(dirc, fn)
            dd = parse_date(fp)
            if dd:
                cands.append((dd, fp))
    cands.sort(key=lambda t: t[0], reverse=True)
    divs = []
    for dd, fp in cands[:3]:
        with open(fp, encoding="utf-8") as f:
            t = f.read()
        ls = t.split("\n")
        bl = ls[1:] if ls and ls[0].startswith("# ") else ls
        divs.append("<div>" + md_to_html("\n".join(bl)) + "</div>")
    description = "\n<hr/>\n".join(divs) if divs else current_html
    # RSS readers read only the text content of <description>; nested HTML
    # elements render as empty. Keep the HTML for content:encoded, but put an
    # escaped flat string into <description>.
    description_escaped = esc(description)

    now = datetime.now(timezone.utc)
    date_key = d.strftime("%Y-%m-%d")
    title_date = d.strftime("%d.%m.%Y")

    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<rss version="2.0" xmlns:content="http://purl.org/rss/1.0/modules/content/">\n'
        "  <channel>\n"
        "    <title>Сводка: российский сегмент</title>\n"
        f"    <link>{esc(args.feed_url)}</link>\n"
        "    <description>Ежедневная сводка по российскому сегменту Telegram</description>\n"
        "    <language>ru</language>\n"
        f"    <lastBuildDate>{format_datetime(now)}</lastBuildDate>\n"
        "    <item>\n"
        f"      <title>Сводка за {title_date}</title>\n"
        f"      <link>{esc(args.item_link)}</link>\n"
        f'      <guid isPermaLink="false">digest-{date_key}</guid>\n'
        f"      <pubDate>{format_datetime(d)}</pubDate>\n"
        f"      <description>{description_escaped}</description>\n"
        f"      <content:encoded><![CDATA[{description}]]></content:encoded>\n"
        "    </item>\n"
        "  </channel>\n"
        "</rss>\n"
    )
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(xml)
    else:
        sys.stdout.write(xml)

if __name__ == "__main__":
    main()
