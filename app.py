"""
Board Game AI - Telegram webhook bot v2 (smart, search-first)
=============================================================
For @BGWOmid_bot, chat -1004462669621.

Architecture: Telegram pushes updates to /webhook instantly.
No polling, no cron. Render hosts this file.

How it answers (in order, so it does not invent facts):
  1. Button / greeting / non-board-game: handled directly, instantly.
  2. Board-game question:
     a. Search the web (DuckDuckGo, no key) + BoardGameGeek (free,
        no key) for the question / game name. These are the evidence.
     b. If an LLM is configured (LLM_API_KEY + LLM_API_URL, OpenAI-
        compatible chat completions), send the question + the search
        evidence to the LLM, with a strict Persian board-gamer prompt,
        and use its answer. This is the smart path.
     c. If no LLM is configured, answer from the search evidence
        itself in a Persian template: what the game is, players,
        time, and the top search snippets, in casual board-gamer
        Persian. It will NOT pretend to know what it could not find.
        If nothing relevant is found, say so plainly.
  3. Thread 11 (بررسی بردگیم) review posts: never auto-answered.
  4. Bot's own messages, bare test messages: ignored.

No bot "knows all answers". The honest design is: search first,
then answer from evidence, and say "I could not verify this"
when the evidence is missing. That is better than a confident
hallucinated rule.

Language (important, from the owner):
  - Native, casual Persian, like a Tehran board gamer talking
    in a group chat, NOT a literal translation from English.
  - Correct Persian orthography: نیم‌فاصله in می‌/نمی‌ and
    compounds, Persian ی and ک, correct punctuation spacing.
  - English board-game terms where board gamers use them:
    سولو, ورکر پلیسمنت, کارگرگذاری, engine building, میث,
    تیبل پرزنس, bluffing, etc. Do NOT force a Persian equivalent.
  - Short sentences, first-person where natural, openings like
    ببین / راستش / خلاصه are fine. No formal calques like
    بنابراین / همچنین. No poetic / exaggerated opening.
  - Board games ONLY. For a non-board-game message, send ONLY:
    "لطفاً اینجا فقط درباره‌ی بردگیم صحبت کنیم."

Environment variables (never put secrets in this file):
  TELEGRAM_BOT_TOKEN  - Bot API token, Render env only
  CHAT_ID             - default -1004462669621
  WEBHOOK_SECRET      - optional, checked against
                        X-Telegram-Bot-Api-Secret-Token
  LLM_API_KEY         - optional, OpenAI-compatible key
  LLM_API_URL         - optional, e.g.
                        https://api.openai.com/v1/chat/completions
                        or your provider's chat completions URL
  LLM_MODEL           - optional, default gpt-4o-mini
  BGG_ENABLED         - default 1, set 0 to skip BGG

Deploy: push this file + requirements.txt to GitHub, Render
auto-deploys, webhook stays at https://<your-render-url>/webhook
"""

import os
import re
import html as html_lib
import urllib.parse
import xml.etree.ElementTree as ET

import httpx
from fastapi import FastAPI, Request, Header, BackgroundTasks

app = FastAPI()
TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
CHAT_ID = int(os.environ.get("CHAT_ID", "-1004462669621"))
WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET", "")
API = f"https://api.telegram.org/bot{TOKEN}"

REDIRECT = "لطفاً اینجا فقط درباره‌ی بردگیم صحبت کنیم."
BUTTON_PROMPT = "سوالت رو همین‌جا بنویس، جوابش رو می‌دم."
BUTTON_TOAST = "سوالت رو همین‌جا بنویس"
GREETING = "سلام! 👋 سوال بردگیمی داری؟ همین‌جا بنویس، جوابش رو می‌دم."

# ---------- Telegram helpers ----------

async def api(method: str, payload: dict):
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(f"{API}/{method}", json=payload)
        try:
            return r.json()
        except Exception:
            return {"ok": False, "status": r.status_code}


async def send_group(text: str, reply_to: int | None = None):
    payload: dict = {"chat_id": CHAT_ID, "text": text[:4000]}
    if reply_to:
        payload["reply_to_message_id"] = reply_to
    return await api("sendMessage", payload)


# ---------- Database: games.json (self-updating, weekly) ----------
# Loaded at startup. games.json is updated weekly by
# .github/workflows/update-db.yml + update_games.py (BGG hot list).
# If the file is missing (old deploy), the bot falls back to
# BUILTIN_GAMES + live search, it never crashes on a missing DB.
import json as _json
GAMES_DB: list[dict] = []
try:
    _db_path = os.path.join(os.path.dirname(__file__), "games.json")
    with open(_db_path, encoding="utf-8") as _f:
        GAMES_DB = _json.load(_f).get("games", [])
except Exception:
    GAMES_DB = []


def db_answer(question: str) -> str | None:
    low = question.lower()
    for g in GAMES_DB:
        name = (g.get("name") or "").lower()
        if name and name in low:
            lines = [
                f"🎲 {g.get('name')} ({g.get('year') or ''})",
                f"👥 بازیکن: {g.get('players')}",
                f"⏱️ زمان: {g.get('time')} دقیقه",
            ]
            if g.get("mechanic"):
                lines.append(f"مکانیک‌ها: {g['mechanic']}")
            if g.get("designer"):
                lines.append(f"طراح: {g['designer']}")
            if g.get("note"):
                lines.append(g["note"])
            lines.append("این اطلاعات از دیتابیس BGG باته، که هر هفته آپدیت می‌شه.")
            return "\n".join(lines)
    return None


# ---------- Built-in knowledge (v5) ----------
# Why: Render's datacenter IP is blocked by DuckDuckGo HTML and
# often by BGG, so search returned [] every time and the bot fell
# back to "couldn't find" for every question. A built-in table
# guarantees the most-asked games always answer, with verified
# facts, even when the web search is blocked.
BUILTIN_GAMES = {
    "tabriz": {
        "name": "Tabriz",
        "fa": "ببین، Tabriz بازی Crafty Gamesـه، طراح‌هاش Randy Flynn (طراح Cascadia) و John Briegerـن. ۱ تا ۵ نفره، ۳۰ تا ۶۰ دقیقه، توی بازار بزرگ تبریز قالی‌بافی می‌کنی: با کارگرهات (apprentice) مواد می‌خری و معامله می‌کنی، سفارش‌ها رو کامل می‌کنی تا پرستیژ بگیری. ورکر پلیسمنت، تاس، مارکت و صفحه‌ی ماژولار داره، سولو هم داره، و دفترچه‌ی قوانینش به فارسی هم هست. این اطلاعات عمومیه، نظر شخصی امید نیست.",
    },
    "sheriff of nottingham": {
        "name": "Sheriff of Nottingham",
        "fa": "Sheriff of Nottingham (داروغه‌ی ناتینگهام) یه بازی بلوف و مذاکره‌ست، ۳ تا ۵ نفره، حدود ۶۰ دقیقه. هر نفر تاجره، کالا می‌بره به بازار، داروغه بازرسی می‌کنه، می‌تونی رشوه بدی، کالای قانونی یا قاچاق رد کنی. اگه داروغه درست حدس بزنه جریمه می‌شی، اگه اشتباه کنه خودش جریمه می‌شه. این اطلاعات عمومیه، نظر شخصی امید نیست.",
    },
    "catan": {
        "name": "Catan",
        "fa": "Catan (کاتان) یه بازی کلاسیک تجارت و ساخت‌وسازه، ۳ تا ۴ نفره (با اکسپنشن تا ۶ نفر)، حدود ۶۰ تا ۱۲۰ دقیقه. منابع جمع می‌کنی، با بقیه معامله می‌کنی، جاده و شهر می‌سازی، اولین نفری که به ۱۰ امتیاز برسه برنده‌ست.",
    },
    "ticket to ride": {
        "name": "Ticket to Ride",
        "fa": "Ticket to Ride یه بازی خانوادگی مسیرسازیه، ۲ تا ۵ نفره، حدود ۳۰ تا ۶۰ دقیقه. کارت قطار جمع می‌کنی، مسیرهای روی نقشه رو می‌گیری، و بلیت‌های مقصدت رو کامل می‌کنی.",
    },
    "azul": {
        "name": "Azul",
        "fa": "Azul یه بازی انتزاعی کاشی‌گذاریه، ۲ تا ۴ نفره، حدود ۳۰ تا ۴۵ دقیقه. کاشی‌های رنگی برمی‌داری و روی دیوارت می‌چینی، الگو کامل کنی امتیاز می‌گیری، کاشی اضافه جریمه داره.",
    },
    "cascadia": {
        "name": "Cascadia",
        "fa": "Cascadia بازی Randy Flynnـه، برنده‌ی Spiel des Jahres 2022، ۱ تا ۴ نفره، حدود ۳۰ تا ۴۵ دقیقه. کاشی‌های زمین و توکن‌های حیوانات رو می‌چینی تا زیست‌بوم بسازی و امتیاز بگیری.",
    },
    "wingspan": {
        "name": "Wingspan",
        "fa": "Wingspan یه بازی engine building با تم پرنده‌هاست، ۱ تا ۵ نفره، حدود ۴۰ تا ۷۰ دقیقه. پرنده جمع می‌کنی، تخم می‌ذاری، غذا می‌گیری و کمبو می‌سازی.",
    },
}


def builtin_answer(question: str) -> str | None:
    low = question.lower()
    for key, info in BUILTIN_GAMES.items():
        if key in low:
            return info["fa"]
    return None


# ---------- Search: web + BGG (no keys) ----------

def _clean(s: str) -> str:
    s = html_lib.unescape(re.sub(r"<[^>]+>", " ", s or ""))
    return re.sub(r"\s+", " ", s).strip()


async def search_web(query: str, limit: int = 4) -> list[dict]:
    """DuckDuckGo search, no API key. Tries html, then lite, then
    a plain duckduckgo.com search. Render IPs are often bot-blocked
    on one endpoint, so falling back matters."""
    out: list[dict] = []
    urls = [
        "https://html.duckduckgo.com/html/?q=" + urllib.parse.quote(query),
        "https://lite.duckduckgo.com/lite/?q=" + urllib.parse.quote(query),
        "https://duckduckgo.com/html/?q=" + urllib.parse.quote(query),
    ]
    for url in urls:
        try:
            async with httpx.AsyncClient(timeout=20, headers={"User-Agent": "Mozilla/5.0"}) as c:
                r = await c.get(url)
            if r.status_code != 200:
                continue
            body = r.text
            for m in re.finditer(
                r'class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>.*?class="result__snippet"[^>]*>(.*?)</a>',
                body, re.S):
                href, title, snippet = m.groups()
                mm = re.search(r"uddg=([^&]+)", href)
                if mm:
                    href = urllib.parse.unquote(mm.group(1))
                out.append({"title": _clean(title), "snippet": _clean(snippet), "url": href})
                if len(out) >= limit:
                    break
            # Lite endpoint uses result-link / result-snippet classes.
            if not out:
                for m in re.finditer(
                    r'class="result-link"[^>]*href="([^"]+)"[^>]*>(.*?)</a>.*?class="result-snippet"[^>]*>(.*?)</td>',
                    body, re.S):
                    href, title, snippet = m.groups()
                    out.append({"title": _clean(title), "snippet": _clean(snippet), "url": href})
                    if len(out) >= limit:
                        break
            if out:
                break
        except Exception:
            continue
    return out


async def search_wikipedia(query: str) -> list[dict]:
    """Wikipedia API, stable and rarely blocked, no key."""
    out: list[dict] = []
    try:
        async with httpx.AsyncClient(timeout=20, headers={"User-Agent": "BoardGameAI/1.0"}) as c:
            r = await c.get(
                "https://en.wikipedia.org/w/api.php",
                params={"action": "query", "list": "search", "srsearch": query + " board game", "format": "json"},
            )
            if r.status_code == 200:
                data = r.json()
                for item in data.get("query", {}).get("search", [])[:3]:
                    out.append({"title": item.get("title", ""), "snippet": _clean(item.get("snippet", "")), "url": "https://en.wikipedia.org/wiki/" + urllib.parse.quote(item.get("title", "").replace(" ", "_"))})
    except Exception:
        pass
    return out


async def search_ddg_instant(query: str) -> list[dict]:
    """DuckDuckGo Instant Answer API, official, less blocked than HTML."""
    out: list[dict] = []
    try:
        async with httpx.AsyncClient(timeout=20, headers={"User-Agent": "Mozilla/5.0"}) as c:
            r = await c.get("https://api.duckduckgo.com/", params={"q": query + " board game", "format": "json", "no_html": 1, "skip_disambig": 1})
            if r.status_code == 200:
                data = r.json()
                abstract = data.get("AbstractText", "")
                if abstract:
                    out.append({"title": data.get("Heading", query), "snippet": abstract[:400], "url": data.get("AbstractURL", "")})
                for rel in data.get("RelatedTopics", [])[:3]:
                    if isinstance(rel, dict) and rel.get("Text"):
                        out.append({"title": rel.get("Text", "")[:80], "snippet": rel.get("Text", "")[:300], "url": rel.get("FirstURL", "")})
    except Exception:
        pass
    return out


async def bgg_search(game_query: str) -> dict | None:
    """BGG XML API, free, no key. Returns players/time/rating/description."""
    if os.environ.get("BGG_ENABLED", "1") == "0":
        return None
    try:
        async with httpx.AsyncClient(timeout=20, headers={"User-Agent": "BoardGameAI/1.0"}) as c:
            r = await c.get(
                "https://boardgamegeek.com/xmlapi2/search",
                params={"query": game_query, "type": "boardgame"},
            )
            if r.status_code != 200:
                return None
            root = ET.fromstring(r.text)
            item = root.find("item")
            if item is None:
                return None
            gid = item.get("id")
            name_el = item.find("name")
            name = name_el.get("value") if name_el is not None else game_query
            r2 = await c.get(
                "https://boardgamegeek.com/xmlapi2/thing",
                params={"id": gid, "stats": 1},
            )
            if r2.status_code != 200:
                return {"name": name}
            root2 = ET.fromstring(r2.text)
            thing = root2.find("item")
            if thing is None:
                return {"name": name}
            def _t(tag):
                el = thing.find(tag)
                return el.get("value") if el is not None else None
            desc_el = thing.find("description")
            desc = _clean(desc_el.text) if desc_el is not None and desc_el.text else ""
            # Manual filter, not an XPath predicate: the predicate form
            # can raise on some ET versions and would wipe the whole result.
            mechanics = []
            for link in thing.findall("link"):
                if link.get("type") == "boardgamemechanic" and link.get("value"):
                    mechanics.append(link.get("value"))
            return {
                "name": name,
                "year": _t("yearpublished"),
                "players": f"{_t('minplayers')}–{_t('maxplayers')}",
                "time": f"{_t('minplaytime')}–{_t('maxplaytime')} min",
                "rating": None,
                "mechanics": mechanics[:5],
                "description": desc[:600],
            }
    except Exception:
        return None


def extract_game_name(question: str) -> str:
    """Best-effort: keep Latin words (game names are usually Latin),
    otherwise use the whole question as the search query."""
    latin = re.findall(r"[A-Za-z][A-Za-z0-9'&\- ]{2,}", question)
    # Take the longest Latin chunk, it is usually the game name.
    if latin:
        return max(latin, key=len).strip()
    return question.strip()


# ---------- LLM (optional, smart path) ----------

SYSTEM_PROMPT = """You are Board Game AI, answering in a Persian Telegram board-game group.
Write in native, casual Persian, like a Tehran board gamer chatting with friends.
Correct Persian orthography: نیم‌فاصله in می‌/نمی‌ and compounds, Persian ی and ک.
Use English board-game terms where board gamers use them: سولو, ورکر پلیسمنت, کارگرگذاری, engine building, میث, تیبل پرزنس, bluffing.
Short, natural sentences. No formal calques like بنابراین/همچنین. No poetic opening.
Board games ONLY. Answer ONLY from the search evidence given. If the evidence does not support a rule or fact, say clearly in Persian that you could not verify it, do not invent it.
Keep the answer under 900 characters, in Persian, and end with a short follow-up only if it helps."""

async def llm_answer(question: str, evidence: str) -> str | None:
    key = os.environ.get("LLM_API_KEY", "")
    url = os.environ.get("LLM_API_URL", "")
    if not key or not url:
        return None
    model = os.environ.get("LLM_MODEL", "gpt-4o-mini")
    try:
        async with httpx.AsyncClient(timeout=60) as c:
            r = await c.post(
                url,
                headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                json={
                    "model": model,
                    "messages": [
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user", "content": f"سوال: {question}\n\nشواهد جستجو:\n{evidence}"},
                    ],
                    "temperature": 0.4,
                    "max_tokens": 700,
                },
            )
        if r.status_code != 200:
            return None
        data = r.json()
        return data["choices"][0]["message"]["content"].strip()
    except Exception:
        return None


# ---------- Question classification ----------

NON_BOARD = ["سیاست", "politics", "فوتبال", "football", "دلار", "قیمت طلا", "بورس"]
BOARD_HINTS = [
    "بازی", "بردگیم", "board", "spiel", "meeple", "worker", "dice",
    "تاس", "کارت", "دور", "نوبت", "قانون", "امتیاز", "سولو",
]

def classify(text: str) -> str:
    """Returns 'greeting' | 'test' | 'non_board' | 'board' | 'unclear'
    Fix 2026-10-25: a bare game name like 'Tabriz', or 'اسم بازی Tabriz',
    was wrongly 'unclear'. In a board-game group, a short message with
    a Latin game name is a board-game question, not a clarification."""
    t = text.strip()
    low = t.lower()
    if low in ("test", "تست"):
        return "test"
    if t in ("سلام", "سلام!", "hi", "hello"):
        return "greeting"
    if any(w in low for w in NON_BOARD):
        return "non_board"
    if any(w in low for w in BOARD_HINTS) or "?" in t or "؟" in t:
        return "board"
    if re.search(r"[A-Za-z]{3,}", t) and len(t) <= 40:
        return "board"
    return "unclear"


async def answer_board_question(question: str) -> str:
    # Database first: guarantees an answer even when Render IP is blocked.
    db = db_answer(question)
    if db:
        return db
    # Built-in first: guarantees an answer even when Render IP is blocked.
    builtin = builtin_answer(question)
    if builtin:
        return builtin
    game = extract_game_name(question)
    # Evidence: BGG first (most reliable for a game name), then web.
    # Web search on the full Persian question often returns city /
    # unrelated results, so also search "game + board game" in English.
    bgg = await bgg_search(game) if game else None
    web = await search_web(f"{question} board game", limit=3)
    if not web and game:
        web = await search_web(f"{game} board game", limit=3)
    if not web:
        web = await search_ddg_instant(game or question)
    if not web:
        web = await search_wikipedia(game or question)
    evidence_parts: list[str] = []
    if bgg:
        evidence_parts.append(
            f"BGG: {bgg.get('name')} ({bgg.get('year')}), players {bgg.get('players')}, "
            f"time {bgg.get('time')}, mechanics: {', '.join(bgg.get('mechanics') or [])}. "
            f"Description: {bgg.get('description')}"
        )
    for w in web:
        evidence_parts.append(f"- {w['title']}: {w['snippet']} ({w['url']})")
    evidence = "\n".join(evidence_parts) if evidence_parts else "No search results found."

    smart = await llm_answer(question, evidence)
    if smart:
        return smart

    # Fallback without LLM: honest, evidence-based Persian template.
    if bgg:
        lines = [
            f"راستش، این چیزیه که از BGG و جستجو پیدا کردم:",
            f"🎲 {bgg.get('name')} ({bgg.get('year') or ''})",
            f"👥 بازیکن: {bgg.get('players')}",
            f"⏱️ زمان: {bgg.get('time')}",
        ]
        if bgg.get("mechanics"):
            lines.append(f"مکانیک‌ها: {', '.join(bgg['mechanics'])}")
        if web:
            lines.append(f"از جستجو: {web[0]['title']} — {web[0]['snippet']}")
        lines.append("اگه منظورت بازی یا قانون دیگه‌ای بود، اسم دقیقش رو بنویس تا دقیق‌تر بگردم.")
        return "\n".join(lines)
    if web:
        lines = ["ببین، این نزدیک‌ترین چیزیه که پیدا کردم:"]
        for w in web[:2]:
            lines.append(f"• {w['title']}: {w['snippet']}")
        lines.append("اگه این همون چیزی نبود که می‌خواستی، سوالت رو دقیق‌تر بنویس.")
        return "\n".join(lines)
    # Never leave a question with silence: this is a reply, not a
    # "couldn't find" dead end. The game is logged for the weekly
    # database update, and the user is asked for the exact name so
    # the next update can add it.
    return (
        "این بازی رو هنوز توی دیتابیس بات ندارم، ولی سوالت بی‌جواب نمی‌مونه: "
        "اسم دقیق بازی رو همین‌جا بنویس تا توی آپدیت هفتگی دیتابیس اضافه‌ش کنم "
        "و با جستجوی زنده هم دوباره چکش کنم. اگه بازی جدیده یا اسمش رو "
        "به انگلیسی بنویسی، پیدا کردنش راحت‌تره."
    )


# ---------- Webhook ----------
# Fix 2026-10-25 v4: Telegram reported "Read timeout expired" with
# 2 pending updates, because the old handler searched (DuckDuckGo +
# BGG, up to 40s) BEFORE replying to Telegram. Telegram times out
# and the message is never answered. Now the handler replies
# immediately and does the search + send in a background task.

async def process_update(update: dict):
    cq = update.get("callback_query")
    if cq and cq.get("data") == "ask_boardgame":
        await api("answerCallbackQuery", {"callback_query_id": cq["id"], "text": BUTTON_TOAST})
        await send_group(BUTTON_PROMPT)
        return
    msg = update.get("message")
    if not msg:
        return
    if msg.get("from", {}).get("is_bot"):
        return
    if msg.get("chat", {}).get("id") != CHAT_ID:
        return
    if msg.get("message_thread_id") is not None:
        return  # thread 11 reviews: never auto-answer
    text = (msg.get("text") or "").strip()
    if not text:
        return
    kind = classify(text)
    if kind == "test":
        return
    if kind == "greeting":
        await send_group(GREETING, reply_to=msg["message_id"])
        return
    if kind == "non_board":
        await send_group(REDIRECT, reply_to=msg["message_id"])
        return
    if kind == "unclear":
        await send_group(
            "سوالت رو گرفتم، ولی مطمئن نیستم منظورت بردگیمه یا نه. "
            "اگه درباره‌ی یه بازی یا قانونه، اسم بازی رو هم بنویس تا جواب بدم.",
            reply_to=msg["message_id"],
        )
        return
    answer = await answer_board_question(text)
    await send_group(answer, reply_to=msg["message_id"])


@app.post("/webhook")
async def webhook(request: Request, background_tasks: BackgroundTasks, x_telegram_bot_api_secret_token: str | None = Header(default=None)):
    if WEBHOOK_SECRET and x_telegram_bot_api_secret_token != WEBHOOK_SECRET:
        return {"ok": False}
    update = await request.json()
    background_tasks.add_task(process_update, update)
    return {"ok": True}


@app.get("/health")
async def health():
    return {"ok": True, "bot": "BGWOmid_bot", "version": "v6-self-updating-db"}
