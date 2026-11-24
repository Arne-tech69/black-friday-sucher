"""Motor des Black-Friday-Suchers.

Holt Deals aus den mydealz-RSS-Feeds, bewertet sie mit der Ampel und
schreibt docs/deals.json. Optional: Push-Alarm ueber ntfy fuer gruene
Deals, die zu einem Profil passen (Umgebungsvariable NTFY_TOPIC).

Nur Standardbibliothek, damit die GitHub Action nichts installieren muss.
"""
import email.utils
import html
import json
import os
import re
import sys
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOCS = os.path.join(ROOT, "docs")
DEALS_FILE = os.path.join(DOCS, "deals.json")
PROFILES_FILE = os.path.join(DOCS, "profiles.json")
SENT_FILE = os.path.join(ROOT, "motor", "sent.json")

# (Feed-URL, ist "heiss"). Der hot-Feed enthaelt nur Deals, die die Community heiss gewaehlt hat.
FEEDS = [
    ("https://www.mydealz.de/rss/hot", True),
    ("https://www.mydealz.de/rss/new", False),
    ("http://feeds.feedburner.com/myDealZ", False),
]
KEEP_DAYS = 7
UA = "Mozilla/5.0 (compatible; black-friday-sucher/1.0; private use)"

GOOD = re.compile(
    r"bestpreis|tiefstpreis|tiefpreis|allzeit|so g[üu]nstig wie nie|niedrigste[rn]? preis|"
    r"neuer? (?:best|tiefst)|günstigster preis seit|preisverlauf|keepa|idealo-?bestpreis",
    re.I,
)
UVP_ONLY = re.compile(r"\buvp\b|statt\s*\d", re.I)
PVG = re.compile(
    r"(?:pvg|preisvergleich|n[äa]chstbeste[rn]?\s*preis|idealo)[^0-9€]{0,60}?(\d{1,5}(?:[.,]\d{1,2})?)\s*(?:€|eur)",
    re.I,
)
PRICE = re.compile(r"(\d{1,5}(?:[.,]\d{1,2})?)\s*(?:€|eur)", re.I)


def num(s):
    if s is None:
        return None
    s = str(s).strip().replace("€", "").replace("EUR", "").strip()
    if not s:
        return None
    if "," in s and "." in s:
        s = s.replace(".", "").replace(",", ".")
    else:
        s = s.replace(",", ".")
    try:
        return round(float(s), 2)
    except ValueError:
        return None


def strip_tags(s):
    s = re.sub(r"<br\s*/?>|</p>|</li>", "\n", s or "", flags=re.I)
    s = re.sub(r"<[^>]+>", " ", s)
    return re.sub(r"[ \t]+", " ", html.unescape(s)).strip()


def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/rss+xml, application/xml"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read()


def local(tag):
    return tag.rsplit("}", 1)[-1]


def parse_feed(raw, hot):
    root = ET.fromstring(raw)
    out = []
    for item in root.iter():
        if local(item.tag) != "item":
            continue
        d = {"hot": hot, "cats": []}
        for ch in item:
            t = local(ch.tag)
            if t == "title":
                d["title"] = (ch.text or "").strip()
            elif t == "link":
                d["link"] = (ch.text or "").strip()
            elif t == "guid" and not d.get("guid"):
                d["guid"] = (ch.text or "").strip()
            elif t == "pubDate":
                try:
                    d["date"] = email.utils.parsedate_to_datetime(ch.text).astimezone(timezone.utc).isoformat()
                except Exception:
                    pass
            elif t == "category" and ch.text:
                d["cats"].append(ch.text.strip())
            elif t in ("description", "encoded"):
                d["text"] = (d.get("text", "") + "\n" + strip_tags(ch.text)).strip()
            elif t == "merchant":
                d["shop"] = ch.get("name") or d.get("shop")
                d["price"] = num(ch.get("price")) if ch.get("price") else d.get("price")
            elif t == "temperature" and ch.text:
                d["temp"] = num(ch.text)
            elif t == "content" and ch.get("url"):
                d["img"] = ch.get("url")
        if d.get("title") and d.get("link"):
            out.append(d)
    return out


def rate(d):
    """Ampel: gruen / gelb / rot und Rabatt gegen den Preisvergleich."""
    text = f"{d.get('title','')}\n{d.get('text','')}"
    price = d.get("price")
    if price is None:
        m = PRICE.search(d.get("title", ""))
        price = num(m.group(1)) if m else None
        d["price"] = price
    pvg = None
    for m in PVG.finditer(text):
        v = num(m.group(1))
        if v and (price is None or v > price):
            pvg = v
            break
    d["pvg"] = pvg
    d["discount"] = round((pvg - price) / pvg * 100) if pvg and price else None
    hot = d.get("hot") or (d.get("temp") or 0) >= 200
    d["hot"] = bool(hot)
    good = bool(GOOD.search(text))
    if hot and good:
        a, why = "gruen", "Heiß und laut Post Bestpreis"
    elif hot and pvg:
        a, why = "gelb", "Heiß, günstiger als der Preisvergleich"
    elif hot:
        a, why = "gelb", "Heiß, aber ohne Beleg aus dem Preisverlauf"
    elif good and pvg:
        a, why = "gelb", "Laut Post Bestpreis, aber noch nicht heiß"
    else:
        a, why = "rot", "Nicht heiß und kein Beleg" + (" (nur gegen UVP gerechnet)" if UVP_ONLY.search(text) else "")
    d["ampel"], d["why"] = a, why
    return d


def load(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def matches(d, prof):
    hay = (d.get("title", "") + " " + " ".join(d.get("cats", []))).lower()
    words = [w.strip().lower() for w in prof.get("keywords", []) if w.strip()]
    hit = any(w in hay for w in words)
    budget = prof.get("budget")
    ok_budget = budget is None or d.get("price") is None or d["price"] <= budget
    return hit and ok_budget


def notify(topic, d, names):
    body = f"{d['title']}\n{d.get('price') or '?'} € bei {d.get('shop') or 'Shop'} · für {', '.join(names)}"
    req = urllib.request.Request(
        f"https://ntfy.sh/{topic}",
        data=body.encode("utf-8"),
        headers={"Title": "Grüner Deal gefunden", "Click": d["link"], "Tags": "green_circle"},
        method="POST",
    )
    urllib.request.urlopen(req, timeout=20).read()


def main():
    now = datetime.now(timezone.utc)
    old = {d["id"]: d for d in load(DEALS_FILE, {}).get("deals", [])}
    errors, fresh = [], {}
    for url, hot in FEEDS:
        try:
            for d in parse_feed(fetch(url), hot):
                d["id"] = re.sub(r"\W+", "", d.get("guid") or d["link"])[-40:]
                prev = fresh.get(d["id"]) or old.get(d["id"])
                if prev and prev.get("hot"):
                    d["hot"] = True  # einmal heiss bleibt heiss
                if prev and prev.get("first"):
                    d["first"] = prev["first"]
                fresh[d["id"]] = rate({**(prev or {}), **d})
        except Exception as e:  # ein kaputter Feed soll den Lauf nicht stoppen
            errors.append(f"{url}: {e}")
    for d in fresh.values():
        d.setdefault("first", now.isoformat())
        d["seen"] = now.isoformat()
    cutoff = now - timedelta(days=KEEP_DAYS)
    merged = {**old, **fresh}
    deals = [d for d in merged.values() if datetime.fromisoformat(d.get("date") or d["first"]) >= cutoff]
    deals.sort(key=lambda d: d.get("date") or d["first"], reverse=True)
    for d in deals:
        if "text" in d:
            d["text"] = d["text"][:600]
    os.makedirs(DOCS, exist_ok=True)
    out = {"updated": now.isoformat(), "errors": errors, "count": len(deals), "deals": deals}
    with open(DEALS_FILE, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, separators=(",", ":"))

    topic = os.environ.get("NTFY_TOPIC", "").strip()
    if topic:
        sent = set(load(SENT_FILE, []))
        profiles = load(PROFILES_FILE, {}).get("profiles", [])
        for d in deals:
            if d["ampel"] != "gruen" or d["id"] in sent:
                continue
            names = [p["name"] for p in profiles if matches(d, p)]
            if names:
                try:
                    notify(topic, d, names)
                    sent.add(d["id"])
                except Exception as e:
                    errors.append(f"ntfy: {e}")
        with open(SENT_FILE, "w", encoding="utf-8") as f:
            json.dump(sorted(sent)[-2000:], f)

    print(f"{len(fresh)} neu/aktualisiert, {len(deals)} gesamt, Fehler: {errors or 'keine'}")
    if not fresh and errors:
        sys.exit(1)


if __name__ == "__main__":
    main()
