#!/usr/bin/env python3
"""Render digest-*.md files to RSS 2.0 XML plus a readable HTML page (stdlib only)."""
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
            out.append('<p><a href="%s">%s</a></p>' % (esc(url), esc(link_label(url))))
        elif s.strip() == "":
            flush()
        else:
            t = s.strip()
            if t.startswith("- "):
                t = t[2:]
            para.append(t)
    flush()
    return "\n".join(out)


def link_label(url):
    """t.me/rybar/84735 -> 'Рыбарь · 84735', прочее -> хвост адреса без схемы."""
    u = re.sub(r"^https?://", "", url).rstrip("/")
    m = re.match(r"t\.me/(?:s/)?([A-Za-z0-9_]+)(?:/(\d+))?", u)
    if m:
        return "%s · %s" % (m.group(1), m.group(2)) if m.group(2) else m.group(1)
    return u if len(u) <= 60 else u[:57] + "…"


def parse_date(path):
    m = DATE_RE.search(os.path.basename(path))
    if m:
        y, mo, d = map(int, m.groups())
        try:
            return datetime(y, mo, d, tzinfo=timezone.utc)
        except ValueError:
            pass
    return None


PAGE_CSS = """
:root{color-scheme:dark light}
*{box-sizing:border-box}
body{margin:0;background:#111214;color:#e6e6e6;
 font:17px/1.6 -apple-system,"Segoe UI",Roboto,"Helvetica Neue",Arial,sans-serif}
main{max-width:760px;margin:0 auto;padding:28px 18px 64px}
h1{font-size:26px;line-height:1.25;margin:0 0 6px}
.sub{color:#9aa0a6;font-size:14px;margin:0 0 22px}
h3{font-size:19px;margin:30px 0 8px;color:#ffb15c;line-height:1.3}
p{margin:8px 0}
a{color:#7fb2ff;word-break:break-word}
hr{border:0;border-top:1px solid #2a2d31;margin:28px 0}
footer{margin-top:40px;padding-top:16px;border-top:1px solid #2a2d31;
 color:#8b9096;font-size:13px}
@media (prefers-color-scheme:light){
 body{background:#fff;color:#1a1a1a}
 .sub,footer{color:#666}
 h3{color:#a35a00}
 a{color:#0b5cd5}
 hr,footer{border-color:#e2e2e2}
}
"""


def page_html(title, subtitle, body_html, footer_html):
    return (
        "<!DOCTYPE html>\n<html lang=\"ru\">\n<head>\n"
        "<meta charset=\"utf-8\">\n"
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">\n"
        "<title>%s</title>\n<style>%s</style>\n</head>\n<body>\n<main>\n"
        "<h1>%s</h1>\n<p class=\"sub\">%s</p>\n%s\n%s\n</main>\n</body>\n</html>\n"
    ) % (esc(title), PAGE_CSS, esc(title), esc(subtitle), body_html, footer_html)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("digest", help="path to a digest-YYYY-MM-DD.md file")
    ap.add_argument("--out", help="write XML to file instead of stdout")
    ap.add_argument("--feed-url", required=True,
                    help="absolute URL of the feed itself (atom:link rel=self)")
    ap.add_argument("--site-url", default="", help="site root for the channel <link>")
    ap.add_argument("--page-base", default="",
                    help="site root used to build item links: {page-base}/d/YYYY-MM-DD.html")
    ap.add_argument("--item-link", default="",
                    help="explicit item <link>; overrides --page-base")
    ap.add_argument("--site-dir", default="",
                    help="write index.html and d/YYYY-MM-DD.html into this directory")
    args = ap.parse_args()

    path = os.path.abspath(args.digest)
    d = parse_date(path)
    if d is None:
        d = datetime.now(timezone.utc)

    with open(path, encoding="utf-8") as f:
        text = f.read()

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
    description_escaped = esc(description)

    now = datetime.now(timezone.utc)
    date_key = d.strftime("%Y-%m-%d")
    title_date = d.strftime("%d.%m.%Y")
    title = "Сводка за " + title_date

    # Item link: prefer an explicit one, then a per-day page, then the site root.
    if args.item_link:
        item_link = args.item_link
    elif args.page_base:
        item_link = args.page_base.rstrip("/") + "/d/" + date_key + ".html"
    else:
        item_link = args.feed_url

    channel_link = args.site_url or args.feed_url

    # HTML pages: today's digest as a real page (the feed's <link> must resolve
    # to content, otherwise readers show an empty article), plus an index.
    if args.site_dir:
        day_dir = os.path.join(args.site_dir, "d")
        os.makedirs(day_dir, exist_ok=True)
        subtitle = "Сводка: российский сегмент · собрано автоматически"
        footer = ('<footer><a href="%s">RSS-лента</a> · '
                  'источники: Telegram-каналы, публичные превью</footer>'
                  % esc(args.feed_url))
        built = now.astimezone().strftime("%d.%m.%Y %H:%M")
        day_page = page_html(title, subtitle + " · " + built, current_html, footer)
        with open(os.path.join(day_dir, date_key + ".html"), "w", encoding="utf-8") as f:
            f.write(day_page)
        with open(os.path.join(args.site_dir, "index.html"), "w", encoding="utf-8") as f:
            f.write(day_page)

    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<rss version="2.0" xmlns:content="http://purl.org/rss/1.0/modules/content/"'
        ' xmlns:atom="http://www.w3.org/2005/Atom">\n'
        "  <channel>\n"
        "    <title>Сводка: российский сегмент</title>\n"
        f"    <link>{esc(channel_link)}</link>\n"
        f'    <atom:link href="{esc(args.feed_url)}" rel="self" type="application/rss+xml"/>\n'
        "    <description>Ежедневная сводка по российскому сегменту Telegram</description>\n"
        "    <language>ru</language>\n"
        f"    <lastBuildDate>{format_datetime(now)}</lastBuildDate>\n"
        "    <item>\n"
        f"      <title>{esc(title)}</title>\n"
        f"      <link>{esc(item_link)}</link>\n"
        f'      <guid isPermaLink="false">digest-{date_key}</guid>\n'
        f"      <pubDate>{format_datetime(now)}</pubDate>\n"
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
