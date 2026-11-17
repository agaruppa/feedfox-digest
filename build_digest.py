#!/usr/bin/env python3
"""Turn state/posts.json into a markdown digest (stdlib only, no LLM).

Input: JSON produced by collect.py. Output: markdown in the same shape as
digest-YYYY-MM-DD.md, ready for render.py (markdown -> RSS 2.0).
"""
import argparse
import json
import re
import sys
import unicodedata
from datetime import datetime, timezone

# Tuning knobs. All thresholds are deliberately simple and documented here.
MAX_CLUSTERS = 10          # at most this many topics, ordered by score
MIN_POSTS_PER_CLUSTER = 2  # skip clusters with fewer posts
MIN_TOTAL_WEIGHT = 2.0     # skip clusters whose summed ratio is weaker than this
MAX_POSTS_PER_TOPIC = 3    # at most this many posts per topic (distinct channels)
TITLE_LIMIT = 90           # ~90 chars for the topic heading
QUOTE_LIMIT = 140          # ~140 chars for the per-channel quote
MIN_TITLE_CHARS = 8        # a shorter first line is treated as unusable

MONTHS_RU = ["января", "февраля", "марта", "апреля", "мая", "июня",
             "июля", "августа", "сентября", "октября", "ноября", "декабря"]

# Minimal stopword set (Russian + English) used only by the divergence
# heuristic. It mirrors the one in collect.py; duplication keeps this file
# self-contained and free of imports.
STOPWORDS = set("""
и в во не на я он что с по как это к но мы она они у из за от то же вы о для ты до
бы а б е г д ж з л м н п р с т у ф х ц ч ш щ э ю й ий ый ая яя ое ее ие ые ии ой ей
их им ими ам ям ом ем у ю я о е ы ь ъ это так уже еще ещё был была было будут есть
the and for are not you this that with was from have has had will would can could
should they them their there here which what when who whom whose how why all any
each more most other some such only own same so than too very just but because
until while about into through during before after above below between under again
further then once here also both few nor not been being do does did doing
""".split())

TOKEN_RE = re.compile(r"[A-Za-zА-Яа-яЁё]{5,}")


def strip_emoji(text):
    """Drop emoji and other symbol glyphs so headers stay plain text.

    Category 'So' covers emoji and dingbats (e.g. U+1F4DD, U+25B6, U+25AA,
    U+2757). ZWJ, variation selectors and keycap are removed separately.
    """
    out = []
    for ch in text:
        o = ord(ch)
        if unicodedata.category(ch) == "So":
            continue
        if 0x200B <= o <= 0x200D or 0xFE00 <= o <= 0xFE0F or o == 0x20E3:
            continue
        out.append(ch)
    return re.sub(r"\s+", " ", "".join(out)).strip()


def clean_edges(text):
    """Strip leftover punctuation around a heading candidate."""
    return text.strip(" \t:;.,!?«»„“”—–-()[]*#").strip()


def truncate_words(text, limit):
    """Cut text to ~limit chars without splitting a word."""
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    cut = text[:limit]
    idx = cut.rfind(" ")
    if idx > limit // 2:
        cut = cut[:idx]
    return cut.rstrip()


def tokens_of(text):
    out = set()
    for w in TOKEN_RE.findall(text.lower()):
        if w not in STOPWORDS:
            out.add(w)
    return out


def first_line_of(text):
    """First non-empty line, cleaned of emoji; '' if nothing usable."""
    for raw in text.split("\n"):
        line = strip_emoji(raw).strip()
        if line:
            return line
    return ""


def topic_heading(post):
    """Heading from the most weighted post: first meaningful line, else 90 chars."""
    line = first_line_of(post.get("text", ""))
    line = clean_edges(line)
    if len(line) < MIN_TITLE_CHARS:
        line = clean_edges(strip_emoji(post.get("text", "")))
    return truncate_words(line, TITLE_LIMIT)


def quote_of(post):
    """~140-char quote from the post body, emoji-free, cut at a word boundary."""
    text = strip_emoji(post.get("text", ""))
    return truncate_words(text, QUOTE_LIMIT)


def cluster_weight(cluster):
    return sum(float(p.get("ratio", 0)) for p in cluster.get("posts", []))


def pick_posts(cluster):
    """Up to MAX_POSTS_PER_TOPIC posts, one per channel, strongest first."""
    posts = sorted(cluster.get("posts", []), key=lambda p: -float(p.get("ratio", 0)))
    seen = set()
    picked = []
    for p in posts:
        ch = p.get("channel")
        if ch in seen:
            continue
        seen.add(ch)
        picked.append(p)
        if len(picked) >= MAX_POSTS_PER_TOPIC:
            break
    return picked


def jaccard(a, b):
    """Set overlap in [0,1]. Returns 0.0 when both are empty."""
    if not a and not b:
        return 0.0
    inter = len(a & b)
    union = len(a | b)
    return inter / union if union else 0.0


def find_divergences(clusters):
    """Find clusters where different channels seem to contradict each other.

    HEURISTIC (not semantic analysis): for the two strongest posts from
    *different* channels we compare word overlap (Jaccard). A low overlap
    while the posts were still clustered together (they share rare tokens)
    suggests the channels frame the same topic very differently.
    """
    divergences = []
    for cl in clusters:
        posts = sorted(cl.get("posts", []), key=lambda p: -float(p.get("ratio", 0)))
        by_channel = {}
        for p in posts:
            by_channel.setdefault(p.get("channel"), p)
        if len(by_channel) < 2:
            continue
        a, b = list(by_channel.values())[:2]
        ta, tb = tokens_of(a.get("text", "")), tokens_of(b.get("text", ""))
        sim = jaccard(ta, tb)
        # Same topic (clustered) yet low word overlap => divergent framing.
        if sim < 0.12 and (ta & tb):
            divergences.append({
                "heading": topic_heading(a),
                "channels": [a.get("title") or a.get("channel"),
                             b.get("title") or b.get("channel")],
                "similarity": sim,
            })
    return divergences


def load_posts(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        print(f"error: posts file not found: {path}", file=sys.stderr)
        sys.exit(2)
    except json.JSONDecodeError as e:
        print(f"error: broken JSON in {path}: {e}", file=sys.stderr)
        sys.exit(2)
    except OSError as e:
        print(f"error: cannot read {path}: {e}", file=sys.stderr)
        sys.exit(2)


def resolve_date(arg, data):
    """Return a datetime.date from --date, else from generated_at, else today."""
    if arg:
        try:
            return datetime.strptime(arg, "%Y-%m-%d").date()
        except ValueError:
            print(f"error: --date must be YYYY-MM-DD, got {arg!r}", file=sys.stderr)
            sys.exit(2)
    gen = data.get("generated_at", "")
    if gen:
        try:
            return datetime.fromisoformat(gen).date()
        except ValueError:
            pass
    return datetime.now(timezone.utc).date()


def format_date(d):
    return f"{d.day} {MONTHS_RU[d.month - 1]} {d.year}"


def build_digest(data, date):
    channels = data.get("channels") or []
    clusters = data.get("clusters") or []

    total_posts = sum(int(c.get("posts", 0)) for c in channels)
    if total_posts <= 0:
        # Fallback: count what we actually have in the payload.
        total_posts = sum(len(c.get("posts", [])) for c in clusters)

    channel_names = [c.get("title") or c.get("name") for c in channels]
    date_str = format_date(date)

    lines = []
    lines.append(f"# Сводка: российский сегмент, {date_str}")
    lines.append("")
    header = f"Собрано из {len(channels)} каналов ({', '.join(channel_names)}), " \
             f"{total_posts} постов за сутки. "
    header += "Собрано автоматически, темы без редакторского разбора."
    lines.append(header)
    lines.append("")

    # Filter and sort clusters by score (strongest first).
    eligible = []
    for cl in clusters:
        if len(cl.get("posts", [])) < MIN_POSTS_PER_CLUSTER:
            continue
        if cluster_weight(cl) < MIN_TOTAL_WEIGHT:
            continue
        eligible.append(cl)
    eligible.sort(key=lambda c: float(c.get("score", 0)), reverse=True)
    eligible = eligible[:MAX_CLUSTERS]

    for i, cl in enumerate(eligible, 1):
        posts = pick_posts(cl)
        if not posts:
            continue
        heading = topic_heading(posts[0])
        lines.append(f"## {i}. {heading}")
        lines.append("")
        for p in posts:
            name = p.get("title") or p.get("channel")
            lines.append(f"— {name}: {quote_of(p)}")
            lines.append(f"- {p.get('url', '')}")
            lines.append("")

    divs = find_divergences(eligible)
    lines.append("## Где каналы расходятся")
    lines.append("")
    if divs:
        for d in divs:
            a, b = d["channels"]
            pct = round(d["similarity"] * 100)
            lines.append(
                f"— «{d['heading']}»: {a} и {b} описывают тему по-разному "
                f"(пересечение слов ~{pct}%).")
        lines.append("")
        lines.append("Эвристика: сравнивается взаимное пересечение слов между "
                     "каналами, а не смысл высказываний.")
    else:
        lines.append("Явных расхождений по эвристике не обнаружено.")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("Собрано автоматически из открытых каналов; темы склеены по "
                 "совпадению слов, без редакторского разбора.")
    return "\n".join(lines).rstrip() + "\n"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--posts", default="state/posts.json",
                    help="path to state/posts.json (default: %(default)s)")
    ap.add_argument("--out", help="output .md path (default: stdout)")
    ap.add_argument("--date", help="override digest date as YYYY-MM-DD")
    args = ap.parse_args()

    data = load_posts(args.posts)
    if not isinstance(data, dict) or "clusters" not in data:
        print(f"error: {args.posts} is not a valid posts.json payload", file=sys.stderr)
        sys.exit(2)

    date = resolve_date(args.date, data)
    text = build_digest(data, date)

    if args.out:
        try:
            with open(args.out, "w", encoding="utf-8") as f:
                f.write(text)
        except OSError as e:
            print(f"error: cannot write {args.out}: {e}", file=sys.stderr)
            sys.exit(2)
    else:
        sys.stdout.write(text)


if __name__ == "__main__":
    main()
