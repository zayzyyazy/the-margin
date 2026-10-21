"""Choose one finite edition (6–8 articles) from collected items. Pure code, no model: every rule is here.

Order of operations
  1. hard filters (ported from weekly/curate.py): live blogs, podcasts, video, deals, sponsored, opinion tags, distressing
  2. window: published in the last WINDOW_DAYS, never anything featured in an earlier edition
  3. volume cap: only the newest PER_SOURCE_POOL items of each source compete
  4. duplicates: same canonical link, or titles that share most of their words, collapse to the strongest item
  5. score: source tier and role, signs of substance, announcement/roundup penalties, recency, feed volume
  6. compose: best item of each strand first, then the rest by score — at most one item per source,
     at most MAX_PER_BUCKET per strand, at most one 'signal' (news) item
"""
import datetime as dt
import math
import re

from .ingest import norm_link

BUCKETS = {
    "ai": "AI & engineering",
    "economy": "Companies & economics",
    "mind": "Mind & society",
    "discovery": "Science & discovery",
}
TARGET, MIN_ITEMS, MAX_ITEMS = 7, 6, 8
MAX_PER_BUCKET, WINDOW_DAYS, PER_SOURCE_POOL = 2, 7, 8

SKIP_TITLE = re.compile(r"^(live|watch|listen|podcast|quiz|video|in pictures|newsletter|morning|evening|today in|"
                        r"kommentar|meinung|marktbericht|liveblog|ticker|opinion|analysis:)\b|"
                        r"\b(live updates|as it happened|deal of the day|best .* deals|coupon|horoscope)\b", re.I)
SPONSORED = re.compile(r"brought to you by|sponsored|advertorial|\banzeige\b|partner content|in partnership with|paid post|"
                       r"\bvideo by\b|\bwatch on [A-Z]|\blisten (now|to)\b|\bpodcast\b|briefing chat|audio long read|^audio\b", re.I)
DISTRESSING = re.compile(r"\bCSAM\b|child (sexual )?abuse|sexual abuse|missbrauch|pervert|paedophil|pedophil|"
                         r"\bcult leader\b|\bmurder trial\b|guilty of murder|\brape\b", re.I)
OPINION = re.compile(r" \| [A-Z][\w.\-]+( [A-Z][\w.\-]+){0,3}$|^(kommentar|meinung|opinion|gastbeitrag|editorial)\b", re.I)
SKIP_URL = re.compile(r"/(videos?|podcasts?|audio|live)/", re.I)

ANNOUNCE = re.compile(r"\b(announc\w*|launch(es|ed|ing)?|introduc(es|ing)|unveil\w*|now available|available now|generally available|"
                      r"release[sd]?\b|v\d+\.\d+|raises \$|funding round|series [a-d]\b|webinar|register now|join us|giveaway|"
                      r"discount|on sale|% off|black friday|pre-?order|keynote|conference recap|save the date)", re.I)
ROUNDUP = re.compile(r"\b(assorted links|links for|link roundup|weekly roundup|this week in|week in review|"
                     r"open thread|news roundup|digest|recap)\b|^\w+ links$|^links\b|hackaday links", re.I)
SUBSTANCE = re.compile(r"\b(how|why|what (it|this|we)|the case (for|against)|inside|explain\w*|lessons?|understand\w*|"
                       r"mechanism|history of|anatomy of|guide|deep dive|in defen[cs]e of|the problem with|rethink\w*|myth)\b", re.I)
TIER = {1: 3.0, 2: 2.0, 3: 1.6, None: 1.6}
ROLE = {"explainer": 1.5, "argument": 1.3, "discovery": 1.2, "primary": 0.6, "signal": 0.0}
STRAND_CUES = {   # a title that is plainly about another strand moves there (OWID on mental health is "mind", not "economy")
    "ai": re.compile(r"\b(AI|LLMs?|language models?|machine learning|neural (nets?|networks?)|agents?|chatbots?|transformers?|GPUs?|inference|"
                     r"fine-tun\w*|benchmarks?|evals?|KI|künstliche intelligenz)\b"),
    "mind": re.compile(r"\b(psycholog\w*|mental health|emotion\w*|loneliness|philosoph\w*|moral\w*|ethic\w*|behavio\w*|happiness|"
                       r"wellbeing|well-being|habits?|grief|friendship|parenting|consciousness|identity|religio\w*|attention span)\b", re.I),
    "economy": re.compile(r"\b(inflation|interest rates?|central bank|tariffs?|GDP|recession|labou?r market|wages?|startups?|industr(y|ies)|"
                          r"manufactur\w*|supply chains?|economy|economic\w*|companies|company|market share)\b", re.I),
}
STOP = set("a an the and or of to in on for with from by at as is are was were be its it this that these those how why what "
           "new into over after about than more most can will just your our their you we they not but has have had der die das und "
           "mit von für ein eine ist im".split())


def strand_of(i):
    hits = [b for b, rx in STRAND_CUES.items() if rx.search(i["title"])]
    return i["bucket"] if not hits or i["bucket"] in hits else hits[0]


def self_promo(i):
    """The publisher writing about itself (its report, its members, its event)."""
    name = re.sub(r"[^\w ]", " ", i["source"]).split()
    words = [w for w in name if len(w) > 3 and w.lower() not in {"blog", "news", "magazine", "engineering", "daily", "science", "world"}]
    return any(re.search(rf"\b{re.escape(w)}\b", i["title"]) for w in words)


def hard_ok(i):
    head = i["title"] + " " + i["excerpt"][:300]
    return not (SKIP_TITLE.search(i["title"]) or OPINION.search(i["title"]) or DISTRESSING.search(head)
                or SPONSORED.search(i["title"] + " " + i["excerpt"][:240]) or SKIP_URL.search(i["link"]))


def title_words(t):
    return {w for w in re.findall(r"[a-zäöüß0-9]+", t.lower()) if len(w) > 2 and w not in STOP}


def same_story(a, b):
    wa, wb = title_words(a["title"]), title_words(b["title"])
    if len(wa) < 3 or len(wb) < 3:
        return False
    return len(wa & wb) / len(wa | wb) >= 0.5


def score(i, now, per_week):
    s, why = TIER[i["tier"]] + ROLE.get(i["role"], 0), []
    ex_words = len(i["excerpt"].split())
    if i["full_words"] >= 1200:
        s += 1.0; why.append("long-form")
    elif i["full_words"] >= 600:
        s += 0.5
    if ex_words >= 40:
        s += 0.5
    elif ex_words < 12:
        s -= 1.5; why.append("no excerpt")
    if SUBSTANCE.search(i["title"]):
        s += 0.6; why.append("explanatory")
    if i["title"].rstrip().endswith("?"):
        s += 0.2
    if ANNOUNCE.search(i["title"]):
        s -= 1.5; why.append("announcement")
    if ROUNDUP.search(i["title"]):
        s -= 2.5; why.append("roundup")
    age = (now - dt.datetime.fromisoformat(i["published"])).total_seconds() / 86400
    s -= min(max(age - 1, 0) * 0.25, 1.5)
    s -= 0.4 * math.log2(1 + per_week.get(i["source"], 0) / 7)       # high-volume feeds compete at a discount
    if self_promo(i):
        s -= 1.2; why.append("about the publisher")
    if i["lang"] != "en":
        s -= 0.3
    if i["access"] == "paywalled":
        s -= 2.0
    return round(s, 3), why


def candidates(items, now, featured):
    """Filtered, windowed, volume-capped, de-duplicated pool, each item scored. `featured` = canonical links + ids already published."""
    since = now - dt.timedelta(days=WINDOW_DAYS)
    per_week = {}
    for i in items:
        if (now - dt.datetime.fromisoformat(i["published"])).days < 7:
            per_week[i["source"]] = per_week.get(i["source"], 0) + 1
    pool, by_src, seen = [], {}, set()
    for i in sorted(items, key=lambda i: i["published"], reverse=True):
        pub = dt.datetime.fromisoformat(i["published"])
        key = norm_link(i["link"])
        if not (since <= pub <= now + dt.timedelta(hours=6)) or key in featured or i["id"] in featured or key in seen or not hard_ok(i):
            continue
        seen.add(key)
        if by_src.get(i["source"], 0) >= PER_SOURCE_POOL:
            continue
        by_src[i["source"]] = by_src.get(i["source"], 0) + 1
        i = dict(i)
        i["source_bucket"], i["bucket"] = i["bucket"], strand_of(i)
        i["score"], i["why"] = score(i, now, per_week)
        pool.append(i)
    pool.sort(key=lambda i: -i["score"])
    unique = []
    for i in pool:                                  # strongest version of each story survives
        twin = next((u for u in unique if same_story(u, i)), None)
        if twin:
            twin.setdefault("also_in", []).append({"source": i["source"], "link": i["link"]})
            continue
        unique.append(i)
    return unique


def compose(pool):
    """Pick the edition from a scored pool. Returns the chosen items in reading order."""
    chosen = []

    def fits(i, cap):
        return (all(c["source"] != i["source"] for c in chosen)
                and sum(c["bucket"] == i["bucket"] for c in chosen) < cap
                and not (i["role"] == "signal" and any(c["role"] == "signal" for c in chosen)))

    for b in BUCKETS:                               # every strand gets its best item first
        best = next((i for i in pool if i["bucket"] == b and fits(i, MAX_PER_BUCKET)), None)
        if best:
            chosen.append(best)
    for cap in (MAX_PER_BUCKET, MAX_PER_BUCKET + 1):  # relax the strand cap only if the day is thin
        for i in pool:
            if len(chosen) >= TARGET:
                break
            if i not in chosen and fits(i, cap):
                chosen.append(i)
        if len(chosen) >= MIN_ITEMS:
            break
    if len(chosen) == TARGET:                       # an 8th only if it is as strong as the edition's median
        med = sorted(c["score"] for c in chosen)[len(chosen) // 2]
        extra = next((i for i in pool if i not in chosen and fits(i, MAX_PER_BUCKET) and i["score"] >= med), None)
        if extra:
            chosen.append(extra)
    lead = max(chosen, key=lambda i: i["score"]) if chosen else None
    rest = [c for c in chosen if c is not lead]
    order, last = [lead] if lead else [], lead["bucket"] if lead else None
    while rest:                                     # alternate strands so neighbours differ
        nxt = next((c for c in rest if c["bucket"] != last), rest[0])
        rest.remove(nxt)
        order.append(nxt)
        last = nxt["bucket"]
    return order
