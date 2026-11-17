#!/usr/bin/env python3
"""Collect Telegram channel posts from public web pages t.me/s/<name>."""
import argparse, html, json, os, re, statistics, sys
from datetime import datetime, timedelta, timezone
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError

UA = "Mozilla/5.0 (Linux; Android 16)"
DEFAULT_CHANNELS = ["rybar", "boris_rozhin", "dva_majors", "milinfolive", "voenkorKotenok"]

STOPWORDS = set("""
и в во не на я он что с по как это к но мы она они у из за от то же вы о для ты до бы
а б е г д ж з л м н п р с т у ф х ц ч ш щ э ю й ею ий ый ая яя ое ее ие ые ии ой ей ых
их им ими ам ям ом ем у ю я о е ы ь ъ это так уже еще ещё был была было были будет есть
the and for are not you this that with was from have has had will would can could should
they them their there here which what when who whom whose how why all any each more most
other some such only own same so than too very just but because until while about into
through during before after above below between under again further then once here also
both few nor not been being do does did doing
""".split())

TOKEN_RE = re.compile(r"[A-Za-zА-Яа-яЁё]{5,}")


def parse_views(s):
    s = s.strip().replace(",", "").replace("\u00a0", "").replace(" ", "").upper()
    if not s:
        return 0
    mult = 1
    if s.endswith("K"):
        mult, s = 1000, s[:-1]
    elif s.endswith("M"):
        mult, s = 1000000, s[:-1]
    try:
        return int(float(s) * mult)
    except ValueError:
        return 0


def strip_tags(frag):
    frag = re.sub(r"<br\s*/?>", "\n", frag)
    frag = re.sub(r"</p>|</blockquote>|</div>", "\n", frag)
    frag = re.sub(r"<[^>]+>", "", frag)
    frag = html.unescape(frag)
    lines = []
    for line in frag.split("\n"):
        line = re.sub(r"[ \t\r]+", " ", line).strip()
        if line:
            lines.append(line)
    return "\n".join(lines)


def extract_messages(page, name):
    posts = []
    chunks = re.split(r'(?=<div class="tgme_widget_message_wrap js-widget_message_wrap">)', page)
    for ch in chunks:
        m = re.search(r'data-post="([^"]+)"', ch)
        if not m:
            continue
        dp = m.group(1)
        if not dp.startswith(name + "/"):
            continue
        mid = dp.split("/", 1)[1]
        tm = re.search(r'<time datetime="([^"]+)"', ch)
        dt = tm.group(1) if tm else None
        v = re.search(r'class="tgme_widget_message_views">([^<]*)<', ch)
        views = parse_views(v.group(1)) if v else 0
        t = re.search(r'class="tgme_widget_message_text js-message_text"[^>]*>(.*?)</div>', ch, re.S)
        text = strip_tags(t.group(1)) if t else ""
        posts.append({"channel": name, "id": mid, "datetime": dt,
                      "views": views, "text": text,
                      "url": f"https://t.me/{name}/{mid}"})
    return posts


def extract_title(page, name):
    m = re.search(r'<meta property="og:title" content="([^"]*)"', page)
    if m:
        return html.unescape(m.group(1)).strip()
    m = re.search(r"<title>([^<]*)</title>", page)
    if m:
        return html.unescape(m.group(1)).replace(" – Telegram", "").replace(" - Telegram", "").strip()
    return name


def tokens_of(text):
    out = set()
    for w in TOKEN_RE.findall(text.lower()):
        if w in STOPWORDS:
            continue
        out.add(w)
    return out


def cluster_posts(posts):
    n = len(posts)
    docfreq = {}
    for p in posts:
        for t in p["_tokens"]:
            docfreq[t] = docfreq.get(t, 0) + 1
    rare = {t for t, c in docfreq.items() if c <= 0.50 * n}
    for p in posts:
        p["_rare"] = p["_tokens"] & rare
    ordered = sorted(posts, key=lambda p: -p["ratio"])
    assigned = set()
    clusters = []
    for i, seed in enumerate(ordered):
        if i in assigned:
            continue
        members = [seed]
        assigned.add(i)
        for j, cand in enumerate(ordered):
            if j in assigned or j == i:
                continue
            if len(seed["_rare"] & cand["_rare"]) >= 2:
                members.append(cand)
                assigned.add(j)
        chans = {m["channel"] for m in members}
        if len(members) >= 2 and len(chans) >= 2:
            clusters.append(members)
    return clusters


def cluster_keywords(members):
    freq = {}
    for m in members:
        for t in m["_rare"]:
            freq[t] = freq.get(t, 0) + 1
    shared = sorted(((c, t) for t, c in freq.items() if c >= 2), key=lambda x: (-x[0], x[1]))
    return [t for c, t in shared][:8]


def fetch(name):
    req = Request(f"https://t.me/s/{name}", headers={"User-Agent": UA})
    with urlopen(req, timeout=20) as r:
        return r.read().decode("utf-8", "replace")


def read_channel_list(path):
    """One channel per line. Blank lines and '#' comments are ignored.
    A channel may be written as 'name', '@name' or a full t.me/s/... link."""
    if not os.path.exists(path):
        print(f"channels file not found: {path}", file=sys.stderr)
        return []
    names = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.split("#", 1)[0].strip()
            if not line:
                continue
            if "/" in line:
                line = line.rstrip("/").rsplit("/", 1)[-1]
            line = line.lstrip("@")
            if line and line not in names:
                names.append(line)
    return names


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--channels", default=",".join(DEFAULT_CHANNELS))
    ap.add_argument("--channels-file", dest="channels_file", default=None,
                    help="text file with one channel per line; '#' starts a comment")
    ap.add_argument("--hours", type=float, default=26)
    ap.add_argument("--out", default="/var/minis/shared/feeds/state/posts.json")
    args = ap.parse_args()
    if args.channels_file:
        names = read_channel_list(args.channels_file)
    else:
        names = [c.strip() for c in args.channels.split(",") if c.strip()]
    if not names:
        print("no channels given", file=sys.stderr)
        return 2
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=args.hours)

    channels_out, all_posts = [], []
    for name in names:
        entry = {"name": name, "title": name, "posts": 0, "median_views": None, "error": None}
        try:
            page = fetch(name)
            if not page.strip():
                entry["error"] = "empty response"
            else:
                entry["title"] = extract_title(page, name)
                kept = []
                for p in extract_messages(page, name):
                    if not p["datetime"]:
                        continue
                    try:
                        dt = datetime.fromisoformat(p["datetime"])
                        if dt.tzinfo is None:
                            dt = dt.replace(tzinfo=timezone.utc)
                    except ValueError:
                        continue
                    if dt >= cutoff:
                        p["title"] = entry["title"]
                        kept.append(p)
                views = [p["views"] for p in kept]
                if views:
                    entry["median_views"] = statistics.median(views)
                entry["posts"] = len(kept)
                all_posts.extend(kept)
        except (URLError, HTTPError, TimeoutError, OSError) as e:
            entry["error"] = f"{type(e).__name__}: {e}"[:200]
        channels_out.append(entry)

    median_map = {c["name"]: c["median_views"] for c in channels_out if c["median_views"]}
    for p in all_posts:
        med = median_map.get(p["channel"])
        p["ratio"] = round(p["views"] / med, 3) if med and med > 0 else 1.0
        p["_tokens"] = tokens_of(p["text"])

    clusters = cluster_posts(all_posts)
    clusters.sort(key=lambda m: -m[0]["ratio"])
    clusters = clusters[:15]
    out_clusters = []
    for members in clusters:
        members = sorted(members, key=lambda p: -p["ratio"])[:6]
        out_clusters.append({
            "id": len(out_clusters) + 1,
            "keywords": cluster_keywords(members),
            "score": round(members[0]["ratio"], 2),
            "posts": [{
                "channel": p["channel"], "title": p["title"], "id": p["id"],
                "url": p["url"], "datetime": p["datetime"], "views": p["views"],
                "ratio": p["ratio"], "text": p["text"][:700],
            } for p in members],
        })

    top = [{
        "channel": p["channel"], "title": p["title"], "id": p["id"],
        "url": p["url"], "datetime": p["datetime"], "views": p["views"],
        "ratio": p["ratio"], "text": p["text"][:500],
    } for p in sorted(all_posts, key=lambda p: -p["ratio"])[:50]]

    result = {"generated_at": now.isoformat(), "channels": channels_out,
              "clusters": out_clusters, "top": top}
    d = os.path.dirname(args.out)
    if d:
        os.makedirs(d, exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    ok = sum(1 for c in channels_out if c["error"] is None)
    print(f"channels ok: {ok}/{len(names)}, posts collected: {len(all_posts)}, "
          f"top: {len(top)}, clusters: {len(out_clusters)}")


if __name__ == "__main__":
    main()
