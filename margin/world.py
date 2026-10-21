"""The World, Explained — significant developments, with the background a non-follower needs.

Pipeline (each step is code except 4 and 6):
  1. ingest the general-news feeds in world_sources.yaml (same ingest as the rest of the paper)
  2. classify every item: reporting / analysis / opinion / live (live pages and opinion never serve as evidence)
  3. cluster items about the same development across outlets (shared proper names within 48 h);
     significance = number of independent outlets reporting it — no geographic quota
  4. fetch and read the articles of each chosen development (trafilatura); a development needs
     at least MIN_TEXTS readable reports of at least MIN_WORDS words, else it becomes an attributed brief
  5. a model drafts four parts — what happened · background · why it matters · what is uncertain — where every
     factual sentence carries a verbatim quote from one of the fetched texts; definitions of terms are labelled separately
  6. checks: the quote must exist in the text, every number must appear in the quoted passage, warnings for dropped
     hedges, causal and scope words; then a second model judges each claim against its evidence window
  7. sentences that fail are removed, never patched; a part left empty, or too little left overall, demotes the
     development to a brief built only from the publishers' own excerpts

Models are called through `llm.call`, which enforces the budget cap before every request.
"""
import collections
import math
import datetime as dt
import hashlib
import json
import re
import unicodedata
import urllib.request
from pathlib import Path

import yaml

from zoneinfo import ZoneInfo

from . import ingest, llm

BERLIN = ZoneInfo("Europe/Berlin")

ROOT = Path(__file__).resolve().parents[1]
MAX_DEVELOPMENTS, MIN_OUTLETS, MIN_TEXTS, MIN_WORDS, MAX_TEXTS = 4, 2, 2, 250, 4
WINDOW_HOURS = 36
LINK_WEIGHT = 5.5
SPORT_URL = re.compile(r"/(sport|sports|fussball|football)/", re.I)
UA = ingest.UA

OPINION_URL = re.compile(r"/(commentisfree|opinions?|kommentar|meinung|columns?)/", re.I)
OPINION_TITLE = re.compile(r"^(opinion|comment|kommentar|meinung|analysis by|editorial)\b|\| [A-Z][\w.\-]+( [A-Z][\w.\-]+){0,3}$", re.I)
ANALYSIS = re.compile(r"^(analysis|explainer|explained|q&a|what we know|what is|why|how)\b|\b(explainer|analysis)\b|/(analysis|explainer)/", re.I)
LIVE = re.compile(r"/(live|liveblog)/|^live\b|\blive updates?\b|liveblog|\bticker\b", re.I)
SKIP = re.compile(r"\b(quiz|crossword|in pictures|photos? of the (day|week)|video|watch|podcast|newsletter|horoscope|"
                  r"sport|football|tennis|cricket|formula one|premier league|bundesliga|olympic\w*|celebrity)\b", re.I)
NAME_STOP = set("the a an and of in on for to with from at by as is are was were be it this that its his her their our new "
                "after over amid says said say will could would may can how why what who when where which first last more "
                "us uk eu un mr ms dr prime minister president government police officials minister state states "
                "monday tuesday wednesday thursday friday saturday sunday january february march april may june july august "
                "september october november december live latest news update analysis opinion video watch report "
                "der die das und ein eine im am zum zur mit von für nach bei auf aus ist sind wird".split())


def kind_of(i):
    t, u = i["title"], i["link"]
    if LIVE.search(u) or LIVE.search(t):
        return "live"
    if OPINION_URL.search(u) or OPINION_TITLE.search(t):
        return "opinion"
    if ANALYSIS.search(t) or ANALYSIS.search(u):
        return "analysis"
    return "reporting"


def names(i):
    """Proper names in title + opening of the excerpt: the identity of a development across outlets and languages."""
    text = i["title"] + ". " + " ".join(i["excerpt"].split()[:30])
    toks = set()
    for m in re.finditer(r"\b([A-ZÄÖÜ][\w'’\-]{2,}(?: [A-ZÄÖÜ][\w'’\-]{2,})*)", text):
        initial = m.start() == 0 or re.search(r"[.!?:]\s*$", text[:m.start()])
        run = [w.lower().strip("'’-").removesuffix("'s").removesuffix("’s") for w in m.group(1).split()]
        if initial and run[0] not in MIDNAMES:
            run = run[1:]
        run = [w for w in run if len(w) > 2 and w not in NAME_STOP]
        if run:
            toks.add(run[-1])          # one token per name: "Donald Trump" → "trump", so a full name is not two shared names
    if i["lang"] == "de":          # German capitalises every noun: keep only names that also occur in English reporting
        toks = {t for t in toks if t in ENGLISH_NAMES}
    return toks


ENGLISH_NAMES, MIDNAMES = set(), set()
WEIGHTY = re.compile(r"\b(inflation|budget|borrowing|deficit|debt|tariffs?|trade|economy|economic|central bank|interest rates?|election\w*|"
                     r"parliament|government|coalition|ceasefire|war|troops|sanctions?|treaty|summit|outbreak|epidemic|ebola|climate|"
                     r"migration|refugees?|court ruling|supreme court|merger|strike|protests?|unrest|wahl|regierung|haushalt)\b", re.I)
PERSONAL = re.compile(r"\b(murder|killed in|stabbing|attacker|car attack|shooting|crash|missing|trial of|sentenced)\b", re.I)


def midnames(pool):
    """Capitalised words that occur somewhere other than the start of a sentence — real names, not 'But' or 'Rising'."""
    out = set()
    for i in pool:
        text = i["title"] + ". " + " ".join(i["excerpt"].split()[:30])
        for m in re.finditer(r"(?<=[\w,;] )([A-ZÄÖÜ][\w'’\-]{2,})", text):
            out.add(m.group(1).lower().strip("'’-").removesuffix("'s").removesuffix("’s"))
    return out


def clusters(items, now):
    since = now - dt.timedelta(hours=WINDOW_HOURS)
    pool = [i for i in items if dt.datetime.fromisoformat(i["published"]) >= since and not SKIP.search(i["title"]) and not SPORT_URL.search(i["link"])]
    for i in pool:
        i["kind"] = kind_of(i)
    pool = [i for i in pool if i["kind"] != "live"]
    seen, uniq = set(), []
    for i in pool:                                 # one article carried by two feeds of the same outlet counts once
        key = ingest.norm_link(i["link"])
        if key not in seen:
            seen.add(key)
            uniq.append(i)
    pool = uniq
    MIDNAMES.clear()
    MIDNAMES.update(midnames(pool))
    ENGLISH_NAMES.clear()
    for i in pool:
        if i["lang"] == "en":
            ENGLISH_NAMES.update(names(i))
    for i in pool:
        i["names"] = names(i)
    # star clustering (not transitive, so one hub name cannot chain unrelated stories): names are weighted by rarity;
    # two items from different outlets are linked when they share ≥ 2 names worth ≥ LINK_WEIGHT together
    n = len(pool)
    df = collections.Counter(x for i in pool for x in i["names"])
    w = {x: math.log(n / c) for x, c in df.items()}
    nbrs = [set() for _ in pool]
    for a in range(n):
        for b in range(a + 1, n):
            if pool[a]["source"] == pool[b]["source"]:
                continue
            shared = pool[a]["names"] & pool[b]["names"]
            if len(shared) >= 2 and sum(w[x] for x in shared) >= LINK_WEIGHT:
                nbrs[a].add(b)
                nbrs[b].add(a)
    free, groups = set(range(n)), {}
    while True:
        centre = max(free, key=lambda k: (len({pool[j]["outlet"] for j in nbrs[k] & free}), len(nbrs[k] & free)), default=None)
        if centre is None or not nbrs[centre] & free:
            break
        members = {centre} | (nbrs[centre] & free)
        free -= members
        groups[centre] = [pool[k] for k in members]
    out = []
    for g in groups.values():
        outlets, leads = set(), {}
        for i in g:                                # syndicated wire copy (same opening words) is one report, not two
            lead = " ".join(norm(i["excerpt"]).lower().split()[:20])
            if lead and lead in leads and leads[lead] != i["outlet"]:
                i["syndicated_with"] = leads[lead]
                continue
            leads.setdefault(lead, i["outlet"])
            outlets.add(i["outlet"])
        if len(outlets) < MIN_OUTLETS:
            continue
        shared = collections.Counter(n for i in g for n in i["names"]).most_common(4)
        newest = max(i["published"] for i in g)
        heads = " ".join(i["title"] for i in g)
        weight = len(outlets) + (1.0 if WEIGHTY.search(heads) else 0) - (0.6 if PERSONAL.search(heads) and not WEIGHTY.search(heads) else 0)
        g.sort(key=lambda i: i["published"], reverse=True)
        g.sort(key=lambda i: i["kind"] != "reporting")          # stable: reporting first, newest first within each kind
        out.append({"items": g,
                    "outlets": sorted(outlets), "significance": len(outlets), "weight": weight, "names": [n for n, _ in shared], "newest": newest})
    out.sort(key=lambda c: (c["weight"], c["newest"]), reverse=True)
    return out


REGION = {
    "europe": "france french germany german merz scholz berlin paris uk britain british london healey starmer eu brussels italy rome "
              "spain madrid poland netherlands dutch sweden norway denmark austria switzerland greece portugal ireland belgium europe european",
    "russia-ukraine": "russia russian putin kremlin moscow ukraine ukrainian zelensky kyiv",
    "mena": "egypt egyptian cairo sisi saudi arabia riyadh iran iranian tehran iraq israel israeli gaza hamas lebanon hezbollah syria "
            "yemen houthi houthis uae emirates qatar jordan libya tunisia morocco algeria palestinian",
    "africa": "nigeria abuja lagos kenya nairobi ethiopia congo ghana sudan senegal uganda tanzania rwanda somalia mali niger africa african",
    "asia-pacific": "china chinese beijing japan tokyo india indian delhi modi pakistan korea seoul taiwan indonesia philippines vietnam "
                    "thailand australia australian bangladesh myanmar",
    "americas": "washington congress senate canada mexico brazil argentina chile colombia venezuela",
}
REGION_OF = {w: r for r, ws in REGION.items() for w in ws.split()}


def region(dev):
    c = collections.Counter(REGION_OF[n] for n in dev["names"] if n in REGION_OF)
    return c.most_common(1)[0][0] if c else "global"


def recent_names(edir, today, days=3):
    out = []
    for p in sorted(edir.glob("*.json")):
        if p.stem < today.isoformat() and (today - dt.date.fromisoformat(p.stem)).days <= days:
            for d in (json.loads(p.read_text()).get("world") or {}).get("developments", []):
                out.append(set(d.get("names", [])))
    return out


def choose(devs, previous):
    """Greedy pick: coverage is one signal; impact, geographic spread and novelty adjust it."""
    picked, left = [], list(devs)
    while left and len(picked) < MAX_DEVELOPMENTS:
        def score(d):
            s, why = d["weight"], []
            same = sum(region(p) == region(d) for p in picked)
            if same and region(d) != "global":
                s -= 1.2 * same; why.append(f"same region as {same} chosen")
            if any(len(set(d["names"]) & prev) >= 3 for prev in previous):
                s -= 1.5; why.append("covered in a recent edition")
            return s, why
        best = max(left, key=lambda d: score(d)[0])
        best["selection"] = {"score": round(score(best)[0], 2), "region": region(best), "adjustments": score(best)[1]}
        picked.append(best)
        left.remove(best)
    return picked


def fetch_text(url):
    import trafilatura                          # imported here so the rest of the paper does not need it
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        html = urllib.request.urlopen(req, timeout=30).read(3_000_000).decode("utf-8", "replace")
    except Exception as e:  # noqa: BLE001
        return None, f"{type(e).__name__} {getattr(e, 'code', '')}".strip()
    text = trafilatura.extract(html, include_comments=False, include_tables=False, favor_precision=True) or ""
    low = text.lower()
    if any(w in low[:600] for w in ("subscribe to continue", "create an account to continue", "this content is for subscribers")):
        return None, "paywalled"
    if len(text.split()) < MIN_WORDS:
        return None, f"only {len(text.split())} words readable"
    return text, None


# ------------------------------------------------------------------ checking (ported from weekly/costtest/harness.py)
def norm(s):
    s = unicodedata.normalize("NFKC", s)
    for a, b in (("’", "'"), ("‘", "'"), ("“", '"'), ("”", '"'), ("„", '"'), ("–", "-"), ("—", "-"), ("…", "..."), ("\xad", "")):
        s = s.replace(a, b)
    return re.sub(r"\s+", " ", s).strip()


def numbers(s):
    out = set()
    for m in re.finditer(r"(?<![\w.,])(\d{1,3}(?:[.,]\d{3})+|\d+(?:[.,]\d+)?)", s):
        t = m.group(1)
        t = re.sub(r"[.,]", "", t) if re.fullmatch(r"\d{1,3}(?:[.,]\d{3})+", t) else t.replace(",", ".")
        out.add(t)
    return out


CAUSAL_CLAIM = re.compile(r"\b(because|due to|led to|leads to|leading to|caused|causes|causing|result(ed)? (of|in)|as a result|driven by|drove|"
                          r"drives|thanks to|blam(e|ed|es|ing)|owing to|so that|therefore|which means|in response to)\b", re.I)
CAUSAL_EVID = re.compile(CAUSAL_CLAIM.pattern[:-3] + r"|after|as|amid|weil|wegen|aufgrund|deshalb|daher|dadurch|führt|verursach\w*|durch|nach)\b", re.I)
SCOPE = re.compile(r"\b(first|only|largest|biggest|highest|lowest|record|all|every|most|never|always|unprecedented|best|worst)\b", re.I)
HEDGES = [["may", "might", "could", "possibly"], ["more than", "over", "at least"], ["about", "around", "roughly", "approximately", "nearly", "almost"],
          ["up to", "as many as"], ["estimated", "estimate"], ["suggest", "suggests", "indicate", "indicates"], ["likely", "probably", "unlikely"],
          ["reportedly", "reported", "according to"], ["expected", "forecast", "plans", "planned", "aims"]]
ATTRIB = re.compile(r"\b(said|says|argued|argues|warn|warns|warned|according to|told|believes?|commentators?|analysts?|"
                    r"economists?|experts?|critics?|observers?|officials?)\b", re.I)


def has(word, text):
    return re.search(r"\b" + re.escape(word) + r"\b", text, re.I) is not None


def window(text, quote, chars=320):
    t, q = norm(text), norm(quote)
    i = t.lower().find(q.lower())
    if i < 0 or len(q) < 12:
        return None
    a, b = max(0, i - chars), min(len(t), i + len(q) + chars)
    return ("…" if a else "") + t[a:b] + ("…" if b < len(t) else "")


def deterministic(s, texts):
    """Returns (hard failures, warnings for the judge, evidence windows)."""
    fails, warns, wins = [], [], []
    if s["kind"] == "interp":
        if re.search(r"\d", s["text"]):
            fails.append("interpretation contains a number")
        if ATTRIB.search(s["text"]):
            fails.append("interpretation attributes a view to someone")
        return fails, warns, wins
    if not s["evidence"]:
        return ["fact without evidence"], warns, wins
    for e in s["evidence"]:
        src = texts.get(e["source"])
        w = window(src["text"], e["quote"]) if src else None
        if w is None:
            fails.append(f"quote not found in {e['source']}")
        else:
            wins.append({"source": e["source"], "outlet": src["outlet"], "date": src["published"][:10], "quote": e["quote"], "window": w,
                         "lang": src["lang"]})
    if fails:
        return fails, warns, wins
    ev = " ".join(w["window"] for w in wins)
    missing = numbers(norm(s["text"])) - numbers(ev)
    if missing:
        fails.append("number(s) not in evidence: " + ", ".join(sorted(missing)))
    if CAUSAL_CLAIM.search(s["text"]) and not CAUSAL_EVID.search(ev):
        warns.append("causal wording without a causal statement in the evidence")
    if all(w["lang"] == "en" for w in wins):
        quotes = " ".join(w["quote"] for w in wins)
        for m in {m.group(1).lower() for m in SCOPE.finditer(s["text"])}:
            if not has(m, ev):
                warns.append(f"scope word '{m}' not in evidence")
        for fam in HEDGES:
            if any(has(h, quotes) for h in fam) and not any(has(h, s["text"]) for h in fam):
                warns.append(f"hedge dropped ({'/'.join(fam[:2])}…)")
        if re.search(r"\b(claim(s|ed)?|alleg\w*|according to|says|said)\b", quotes, re.I) and not re.search(
                r"\b(claim(s|ed)?|alleg\w*|argu\w*|according to|believes?|says|said|told|stated|warned|expects?|estimated?)\b", s["text"], re.I):
            warns.append("source reports a claim; sentence does not attribute it")
    return fails, warns, wins


# ------------------------------------------------------------------ model stages
SYSTEM = """You write "The World, Explained" for The Margin, a calm, intelligent daily newspaper. The reader is intelligent and curious
but does not follow every ongoing story. British English.

Rules — they are checked automatically, sentence by sentence:
1. Use ONLY the source texts provided for facts. Never add facts, numbers, names, dates or quotations from memory.
2. Every sentence is an object. kind="fact" for anything checkable (an event, number, name, date, a claim attributed to someone).
   kind="interp" only for your own framing that adds no new fact, contains no numbers and attributes no view to anyone.
3. Every fact sentence carries evidence: the source id and a quote copied character for character from that source (one contiguous
   passage, 8–40 words) that supports the WHOLE sentence — numbers, causes, scope, timing and attribution. No passage, no sentence.
4. Never turn a hedge into certainty, a correlation into a cause, or someone's claim into a fact: attribute claims ("X said").
5. German sources: write in English and quote the German original verbatim as evidence.
6. Introduce people and institutions briefly the first time ("Friedrich Merz, Germany's chancellor"), but only with descriptions
   the sources give. Spell out every acronym once. No jargon left unexplained, and no talking down.
7. Plain, precise prose. No hype, no clichés, no rhetorical questions, no exclamation marks."""

SENT = {"type": "object", "required": ["text", "kind", "evidence"], "properties": {
    "text": {"type": "string"}, "kind": {"type": "string", "enum": ["fact", "interp"]},
    "evidence": {"type": "array", "items": {"type": "object", "required": ["source", "quote"],
                                             "properties": {"source": {"type": "string"}, "quote": {"type": "string"}}}}}}
PARTS = {"happened": "What happened", "background": "The background", "matters": "Why it matters", "uncertain": "What is uncertain or disputed"}
DRAFT_SCHEMA = {"type": "object", "required": ["headline", "parts", "terms"], "properties": {
    "headline": {"type": "string"},
    "parts": {"type": "object", "required": list(PARTS), "properties": {k: {"type": "array", "items": SENT} for k in PARTS}},
    "terms": {"type": "array", "items": {"type": "object", "required": ["term", "definition"],
                                         "properties": {"term": {"type": "string"}, "definition": {"type": "string"}}}}}}
JUDGE_SCHEMA = {"type": "object", "required": ["verdicts"], "properties": {"verdicts": {"type": "array", "items": {
    "type": "object", "required": ["id", "verdict", "numbers_ok", "causality_ok", "scope_ok", "attribution_ok", "context_ok", "on_topic", "note"],
    "properties": {"id": {"type": "string"}, "verdict": {"type": "string", "enum": ["supported", "partial", "unsupported", "contradicted"]},
                   "numbers_ok": {"type": "boolean"}, "causality_ok": {"type": "boolean"}, "scope_ok": {"type": "boolean"},
                   "attribution_ok": {"type": "boolean"}, "context_ok": {"type": "boolean"}, "on_topic": {"type": "boolean"},
                   "note": {"type": "string"}}}}}}
COHERENCE_SCHEMA = {"type": "object", "required": ["coherent", "problems"], "properties": {
    "coherent": {"type": "boolean"}, "problems": {"type": "array", "items": {"type": "string"}}}}
TERM_SCHEMA = {"type": "object", "required": ["terms"], "properties": {"terms": {"type": "array", "items": {
    "type": "object", "required": ["term", "ok", "note"], "properties": {"term": {"type": "string"}, "ok": {"type": "boolean"}, "note": {"type": "string"}}}}}}

JUDGE = """Keep each note under 20 words.
You are a strict fact-checker for a newspaper. For each claim, decide whether its evidence windows support the claim AS WRITTEN.
Check every part: numbers (exact values, units, periods), causality (the claim may say X caused Y only if the evidence says so),
scope and certainty (no dropped hedges; no "first/only/record/all" unless the evidence says it), attribution (the right person or
organisation says it; a claim reported by someone is not stated as fact), and context (the claim does not distort the passage).
supported = every part is supported. partial = some part is not. unsupported = the evidence does not back it. contradicted = the
evidence says otherwise. on_topic = the claim is about the development named at the top, not a side story that happens to appear
in the same article. Use ONLY the evidence windows — no outside knowledge. Automated warnings are hints, not verdicts."""

COHERENCE = """You are the final editor. Below is a short explanation written for a reader who has not followed the story, in four parts.
Set coherent=false and list each problem (under 20 words) if ANY of these is true: a sentence is about a different story;
two sentences contradict or sit awkwardly against each other; something is referred to as if the reader already knew it (an
unexplained "the sanctions", "the deal", a person or acronym never introduced); a part does not do its job (background that is
not background, "uncertain" that states settled facts); the parts do not add up to an understandable account. Otherwise coherent=true."""

TERMS = """Each item is a short definition written for a general reader. Mark ok=true only if it is a neutral, uncontroversial, generally
accepted definition of the term or institution, contains no number or date, and makes no claim about current events or anyone's
intentions. Otherwise ok=false and say why in under 12 words."""


def draft_prompt(dev, texts):
    blocks = "\n\n".join(f'<source id="{sid}" outlet="{t["outlet"]}" type="{t["kind"]}" published="{t["published"][:10]}" lang="{t["lang"]}">\n'
                         f'{t["title"]}\n\n{t["text"][:9000]}\n</source>' for sid, t in texts.items())
    return ("Explain one development for a reader who has not followed it. Write four parts, each 2–4 sentences: "
            "'happened' (what happened, concretely), 'background' (what came before, who the actors are), 'matters' (consequences "
            "the sources describe — attribute forecasts), 'uncertain' (what the sources say is unknown, disputed or still to be decided). "
            "Headline: plain, at most 12 words, no question. 'terms': 1–3 unfamiliar terms or institutions in this story with a one-line "
            "general definition (no numbers or dates; these are labelled as general definitions, not sourced reporting). "
            "Sources typed 'analysis' may be used for interpretation only when attributed.\n\nThe development, as identified by outlets "
            f"{', '.join(dev['outlets'])}: {', '.join(dev['names'])}\n\n{blocks}")


def judge_prompt(items, dev_label=""):
    parts = [f"The development: {dev_label}"] if dev_label else []
    for cid, s, warns, wins in items:
        ev = "\n".join(f'  [{w["source"]} · {w["outlet"]} · {w["date"]}] {w["window"]}' for w in wins)
        parts.append(f'<claim id="{cid}">\n{s["text"]}\n<evidence>\n{ev}\n</evidence>' + (f"\n<warnings>{'; '.join(warns)}</warnings>" if warns else "") + "\n</claim>")
    return JUDGE + "\n\n" + "\n\n".join(parts)


def passes(v):
    return bool(v) and v["verdict"] == "supported" and all(v.get(k) for k in ("numbers_ok", "causality_ok", "scope_ok", "attribution_ok",
                                                                                 "context_ok", "on_topic"))


def explain(dev, texts, cfg, ledger):
    draft = llm.call(cfg["writer"], SYSTEM, draft_prompt(dev, texts), DRAFT_SCHEMA, 2600, ledger, "world:draft")
    claims, report = [], {"removed": [], "checked": 0}
    for part in PARTS:
        for k, s in enumerate(draft["parts"].get(part, [])):
            s.setdefault("evidence", [])
            cid = f"{part}.{k}"
            fails, warns, wins = deterministic(s, texts)
            s["_wins"] = wins
            if fails:
                report["removed"].append({"id": cid, "text": s["text"], "why": fails})
                s["_ok"] = False
            elif s["kind"] == "fact":
                claims.append((cid, s, warns, wins))
            else:
                s["_ok"] = True
    if claims:
        label = f'{draft["headline"]} ({", ".join(dev["names"])})'
        verdicts = {v["id"]: v for v in llm.call(cfg["judge"], "", judge_prompt(claims, label), JUDGE_SCHEMA, 120 * len(claims) + 800,
                                                  ledger, "world:judge")["verdicts"]}
        for cid, s, warns, wins in claims:
            v = verdicts.get(cid)
            s["_ok"] = passes(v)
            report["checked"] += 1
            if not s["_ok"]:
                report["removed"].append({"id": cid, "text": s["text"], "why": [(v or {}).get("verdict", "no verdict"), (v or {}).get("note", "")]})
    terms = draft.get("terms", [])[:3]
    if terms:
        tv = {t["term"]: t for t in llm.call(cfg["judge"], "", TERMS + "\n\n" + json.dumps(terms, ensure_ascii=False), TERM_SCHEMA, 400,
                                             ledger, "world:terms")["terms"]}
        terms = [t for t in terms if (tv.get(t["term"]) or {}).get("ok") and not re.search(r"\d", t["definition"])]
    parts = {}
    for part in PARTS:
        kept = [{"text": s["text"], "kind": s["kind"], "cites": sorted({w["source"] for w in s["_wins"]})}
                for s in draft["parts"].get(part, []) if s.get("_ok")]
        parts[part] = kept
    # Publish only an explanation that survived whole: any removed sentence, or any empty part, means a brief instead —
    # surviving fragments of a checked draft are not a coherent account.
    ok = not report["removed"] and all(parts[p] for p in PARTS)
    report["coherence"] = None
    if ok:
        piece = draft["headline"] + "\n\n" + "\n\n".join(f'{PARTS[p]}:\n' + " ".join(x["text"] for x in parts[p]) for p in PARTS)
        c = llm.call(cfg["judge"], "", COHERENCE + "\n\n" + piece, COHERENCE_SCHEMA, 500, ledger, "world:coherence")
        report["coherence"] = c
        ok = bool(c.get("coherent")) and not c.get("problems")
    return {"headline": draft["headline"], "parts": parts, "terms": terms, "complete": ok, "report": report}


def central(dev, i):
    """How squarely an item is about the development: shared names with the development's top names, from the headline."""
    core = set(dev["names"][:4])
    return len(core & names_in_title(i))


def names_in_title(i):
    return {w for w in re.findall(r"[a-zäöüß\-]+", i["title"].lower()) if len(w) > 2}


def brief_of(dev):
    """Fallback: only the publishers' own words, each attributed — the two reports most squarely about the development
    (headline shares the development's names), English reporting first, one per outlet, syndicated copies skipped."""
    pool = [i for i in dev["items"] if i["kind"] in ("reporting", "analysis") and i.get("excerpt") and not i.get("syndicated_with")]
    pool.sort(key=lambda i: (-central(dev, i), i["kind"] != "reporting", i["lang"] != "en"))
    out, seen = [], set()
    for i in pool:
        if i["outlet"] in seen or central(dev, i) < 2:
            continue
        seen.add(i["outlet"])
        out.append({"outlet": i["outlet"], "title": i["title"], "link": i["link"], "excerpt": i["excerpt"], "kind": i["kind"],
                    "published": i["published"], "lang": i["lang"]})
        if len(out) == 2:
            break
    return {"reports": out}


def build_world(now, cfg, ledger, fetch=ingest.collect, edir=None):
    sources = yaml.safe_load((ROOT / "margin" / "world_sources.yaml").read_text())
    reports, items = fetch(sources)
    for i in items:
        i["outlet"] = i["source"].split(" · ")[0]
    devs = clusters(items, now)
    previous = recent_names(edir, now.astimezone(BERLIN).date()) if edir else []
    chosen, notes = [], []
    for dev in choose(devs, previous):
        texts, tried = {}, []
        seen_outlets = set()
        for i in dev["items"]:
            if i["kind"] == "opinion" or i["outlet"] in seen_outlets or len(texts) >= MAX_TEXTS:
                continue
            text, err = fetch_text(i["link"])
            tried.append({"outlet": i["outlet"], "link": i["link"], "error": err})
            if text:
                sid = f"S{len(texts) + 1}"
                texts[sid] = {"outlet": i["outlet"], "title": i["title"], "link": i["link"], "published": i["published"],
                              "kind": i["kind"], "lang": i["lang"], "text": text, "words": len(text.split())}
                seen_outlets.add(i["outlet"])
        entry = {"selection": dev.get("selection"), "id": hashlib.sha1(("|".join(sorted(i["link"] for i in dev["items"]))).encode()).hexdigest()[:10],
                 "significance": dev["significance"], "outlets": dev["outlets"], "names": dev["names"],
                 "sources": [{k: v for k, v in t.items() if k != "text"} | {"id": sid} for sid, t in texts.items()],
                 "also": [{"outlet": i["outlet"], "title": i["title"], "link": i["link"], "kind": i["kind"], "published": i["published"]}
                          for i in dev["items"] if i["link"] not in {t["link"] for t in texts.values()}][:6],
                 "fetch_failures": [t for t in tried if t["error"]]}
        if len(texts) < MIN_TEXTS:
            entry.update(mode="brief", brief=brief_of(dev), why=f"only {len(texts)} readable report(s)")
        else:
            try:
                ex = explain(dev, texts, cfg, ledger)
            except llm.BudgetExceeded as e:
                entry.update(mode="brief", brief=brief_of(dev), why=f"budget: {e}")
                chosen.append(entry)
                notes.append(str(e))
                break
            except Exception as e:  # noqa: BLE001 — one development failing must not sink the paper
                entry.update(mode="brief", brief=brief_of(dev), why=f"model step failed: {type(e).__name__}: {str(e)[:120]}")
            else:
                entry.update(mode="explained" if ex["complete"] else "brief", explained=ex,
                             brief=None if ex["complete"] else brief_of(dev),
                             why=None if ex["complete"] else "too little survived checking")
        if entry["mode"] == "brief" and not entry["brief"]["reports"]:
            notes.append(f"dropped {', '.join(dev['names'][:3])}: no report squarely about it")
            continue
        chosen.append(entry)
    return {"developments": chosen, "clusters_found": len(devs), "items": len(items),
            "sources_ok": sum(r["ok"] for r in reports), "sources_active": len(reports),
            "sources_failed": [r["name"] for r in reports if not r["ok"]], "notes": notes,
            "models": {"writer": cfg["writer"], "judge": cfg["judge"]}}
