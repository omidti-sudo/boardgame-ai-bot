"""
Board Game AI - Telegram webhook bot (for @BGWOmid_bot)
=====================================================
Architecture (why this is instant, unlike polling):
  Telegram -> POST update -> this server (webhook) -> reply via Bot API
  No polling loop, no 1-minute cron. Telegram pushes the update
  the second it arrives, and this server answers right away.

What this server does:
  - Button tap (callback_data "ask_boardgame"): answerCallbackQuery
    immediately, then send the group prompt message once.
  - New message in the general topic (no message_thread_id) in
    chat -1004462669621 (گپ بردگیمی):
      * board-game question/discussion -> answer in Persian
      * non-board-game -> ONLY "لطفاً اینجا فقط درباره‌ی بردگیم صحبت کنیم."
      * greeting (سلام etc.) -> short greeting + invite a board-game question
  - Thread 11 (بررسی بردگیم) review posts: ignored, never auto-answered.
  - Bot's own messages and bare test messages ("test", "تست"): ignored.

AI answering:
  This starter version uses a simple, honest fallback: it does NOT
  pretend to know a board-game answer it has not verified. For a
  real board-game question it replies that the question was received
  and asks for a little more detail, unless you wire in an LLM API
  (see LLM_ANSWER below). To get full intelligent answers like the
  Spiel des Jahres example, set LLM_API_KEY + LLM_API_URL in the
  environment and implement LLM_ANSWER() for your provider
  (OpenAI / Anthropic / etc. - each provider has a different
  request shape, so do not guess it).

Environment variables (never put the token in this file):
  TELEGRAM_BOT_TOKEN  - Bot API token, from BotFather, full token
  CHAT_ID             - default -1004462669621
  WEBHOOK_SECRET      - optional secret Telegram sends in
                        X-Telegram-Bot-Api-Secret-Token header
  LLM_API_KEY         - optional, for intelligent answers
  LLM_API_URL         - optional, provider chat completions URL

Run locally (for testing only, Telegram cannot reach localhost):
  pip install fastapi uvicorn httpx
  TELEGRAM_BOT_TOKEN=... uvicorn app:app --port 8000

Deploy (webhook, public HTTPS required):
  1. Deploy this file to Render / Vercel / Cloudflare Workers
     (or any host with a public https:// URL).
  2. Set the environment variables above on the host.
  3. Register the webhook once:
     curl "https://api.telegram.org/bot<token>/setWebhook?url=<your-public-url>/webhook&allowed_updates=[\"message\",\"callback_query\"]&secret_token=<WEBHOOK_SECRET>"
     (do this from a terminal, never paste the token in chat)
  4. To stop the webhook and go back to polling:
     curl "https://api.telegram.org/bot<token>/deleteWebhook?drop_pending_updates=true"

Security: never commit a real token, never log the token,
never return it in any response.
"""

import os
import httpx
from fastapi import FastAPI, Request, Header

app = FastAPI()
TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
CHAT_ID = int(os.environ.get("CHAT_ID", "-1004462669621"))
WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET", "")
API = f"https://api.telegram.org/bot{TOKEN}"

REDIRECT = "لطفاً اینجا فقط درباره‌ی بردگیم صحبت کنیم."
BUTTON_PROMPT = "سوالت رو همین‌جا بنویس، جوابش رو می‌دم."
BUTTON_TOAST = "سوالت رو همین‌جا بنویس"


async def api(method: str, payload: dict):
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(f"{API}/{method}", json=payload)
        return r.json()


async def send_group(text: str, reply_to: int | None = None):
    payload: dict = {"chat_id": CHAT_ID, "text": text}
    if reply_to:
        payload["reply_to_message_id"] = reply_to
    return await api("sendMessage", payload)


def is_board_game(text: str) -> bool | None:
    """Return True for board-game, False for clearly non-board-game,
    None for greeting/unclear (handled separately)."""
    t = text.strip().lower()
    if t in ("سلام", "hi", "hello", "سلام!"):
        return None
    if t in ("test", "تست"):
        return None
    # Very small keyword gate for the starter version.
    # A real version should use an LLM classifier here.
    board_words = [
        "بازی", "بردگیم", "board", "spiel", "بازی‌های", "بازی ها",
        "meeple", "worker", "dice", "card", "deck", "solo", "co-op",
    ]
    non_board_words = ["سیاست", "politics", "football", "فوتبال", "قیمت دلار"]
    if any(w in t for w in non_board_words):
        return False
    if any(w in t for w in board_words) or "?" in t or "؟" in t:
        return True
    return False


async def llm_answer(question: str) -> str | None:
    """Optional LLM hook. Returns None when no LLM is configured,
    so the caller falls back to an honest acknowledgement."""
    key = os.environ.get("LLM_API_KEY", "")
    url = os.environ.get("LLM_API_URL", "")
    if not key or not url:
        return None
    # Implement your provider's request shape here, in Persian,
    # board-gamer language, English board-game terms where normal,
    # board games only. Do not invent rules you cannot verify.
    return None


@app.post("/webhook")
async def webhook(request: Request, x_telegram_bot_api_secret_token: str | None = Header(default=None)):
    if WEBHOOK_SECRET and x_telegram_bot_api_secret_token != WEBHOOK_SECRET:
        return {"ok": False}
    update = await request.json()

    # Button tap - answer instantly, this is the time-critical path.
    cq = update.get("callback_query")
    if cq and cq.get("data") == "ask_boardgame":
        await api("answerCallbackQuery", {
            "callback_query_id": cq["id"], "text": BUTTON_TOAST,
        })
        await send_group(BUTTON_PROMPT)
        return {"ok": True}

    msg = update.get("message")
    if not msg:
        return {"ok": True}
    # Skip bot's own messages, thread 11 reviews, and other chats.
    if msg.get("from", {}).get("is_bot"):
        return {"ok": True}
    if msg.get("chat", {}).get("id") != CHAT_ID:
        return {"ok": True}
    if msg.get("message_thread_id") is not None:
        return {"ok": True}
    text = (msg.get("text") or "").strip()
    if not text:
        return {"ok": True}

    kind = is_board_game(text)
    if kind is None:
        # Greeting / test - do not treat as a board-game question.
        if text.lower() in ("test", "تست"):
            return {"ok": True}
        await send_group(
            "سلام! 👋 سوال بردگیمی داری؟ همین‌جا بنویس، جوابش رو می‌دم.",
            reply_to=msg["message_id"],
        )
        return {"ok": True}
    if kind is False:
        await send_group(REDIRECT, reply_to=msg["message_id"])
        return {"ok": True}

    # Board-game question: try LLM, else honest fallback.
    answer = await llm_answer(text)
    if not answer:
        answer = (
            "سوالت رو گرفتم. برای اینکه جواب دقیق بدم، بگو منظورت کدوم بازی "
            "یا کدوم قانونه؟ همین‌جا بنویس تا کامل جواب بدم."
        )
    await send_group(answer, reply_to=msg["message_id"])
    return {"ok": True}


@app.get("/health")
async def health():
    return {"ok": True, "bot": "BGWOmid_bot"}
