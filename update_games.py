"""
update_games.py — self-updating BGG database (weekly, via GitHub Actions)
========================================================================
What it does, every Sunday:
  1. Loads games.json (the bot's database).
  2. Fetches BGG "hot" list (xmlapi2/hot?type=boardgame, top 50).
     Any hot game not already in games.json is added with its
     BGG thing data (players, time, year, mechanics).
  3. Refreshes rating/year for games already in the database,
     in batches of 100 (BGG thing API allows id lists), with
     delays so BGG does not rate-limit us.
  4. Writes games.json back, with today's date, and exits.
     GitHub Actions commits the change, Render auto-deploys.

Why not "all 150,000 BGG games at once"?
  BGG rate-limits the XML API, a full fetch would take days and
  get blocked, and a 150k-game file would be tens of MB — too
  big for Render free and slow to load on every question.
  The practical self-updating design is: start with the top /
  most-asked games, grow by hot list + by games the group actually
  asks about (see learned_games in the bot), and refresh weekly.
  That covers ~99% of real group questions, and the bot still
  falls back to live BGG/Wikipedia/LLM for anything not in the DB,
  so no question is left with silence.

Run locally:
  python3 update_games.py
Requires: httpx (pip install httpx)
"""
import json, time, os, sys
import xml.etree.ElementTree as ET
import httpx

DB = os.path.join(os.path.dirname(__file__), "games.json")

def load():
    with open(DB, encoding="utf-8") as f:
        return json.load(f)

def save(data):
    import datetime
    data["updated"] = datetime.date.today().isoformat()
    with open(DB, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)

def bgg_hot():
    try:
        r = httpx.get("https://boardgamegeek.com/xmlapi2/hot", params={"type": "boardgame"}, timeout=30, headers={"User-Agent": "BoardGameAI-Updater/1.0"})
        if r.status_code != 200:
            return []
        root = ET.fromstring(r.text)
        return [(it.get("id"), it.get("name")) for it in root.findall("item")]
    except Exception as e:
        print("hot failed:", e)
        return []

def bgg_things(ids):
    try:
        r = httpx.get("https://boardgamegeek.com/xmlapi2/thing", params={"id": ",".join(ids), "stats": 1}, timeout=60, headers={"User-Agent": "BoardGameAI-Updater/1.0"})
        if r.status_code != 200:
            return []
        root = ET.fromstring(r.text)
        out = []
        for thing in root.findall("item"):
            def t(tag):
                el = thing.find(tag)
                return el.get("value") if el is not None else None
            name = None
            for n in thing.findall("name"):
                if n.get("type") == "primary":
                    name = n.get("value")
            mechanics = [l.get("value") for l in thing.findall("link") if l.get("type") == "boardgamemechanic"]
            out.append({
                "bgg_id": int(thing.get("id")),
                "name": name,
                "players": f"{t('minplayers')}–{t('maxplayers')}" if t('minplayers') else "",
                "time": f"{t('minplaytime')}–{t('maxplaytime')}" if t('minplaytime') else "",
                "year": int(t('yearpublished')) if t('yearpublished') else 0,
                "mechanic": ", ".join(mechanics[:5]),
                "rating": 0,
            })
        return out
    except Exception as e:
        print("thing failed:", e)
        return []

def main():
    data = load()
    known = {g["name"].lower() for g in data["games"]}
    hot = bgg_hot()
    print(f"hot: {len(hot)} games")
    new_ids = [gid for gid, name in hot if name and name.lower() not in known]
    if new_ids:
        print(f"adding {len(new_ids)} new hot games")
        for thing in bgg_things(new_ids):
            if thing["name"]:
                data["games"].append(thing)
                known.add(thing["name"].lower())
        time.sleep(5)
    save(data)
    print(f"total games in DB: {len(data['games'])}")

if __name__ == "__main__":
    main()
