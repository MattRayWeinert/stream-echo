#!/usr/bin/env python3
"""
Twitch chat bot: when someone @mentions your account (or starts a message with your login),
answer using an OpenAI-compatible chat API (Ollama, OpenAI, Groq, etc.).

Requires (after `source ./secrets.sh`):
  TWITCH_CHATBOT_OAUTH / TWITCH_CHATBOT_NICK (or TWITCH_OAUTH / TWITCH_NICK)
  TWITCH_CHANNEL

LLM (Ollama example in secrets.sh):
  OPENAI_BASE_URL          http://127.0.0.1:11434/v1 for Ollama
  OPENAI_MODEL             e.g. llama3.2
  OPENAI_API_KEY           optional for Ollama (any placeholder string)
  TWITCH_CHATBOT_ALLOWED_USERS  Comma-separated logins, or * / all / empty for everyone
  TWITCH_CHATBOT_COOLDOWN      Seconds between replies per user (default 15)
  TWITCH_CHATBOT_SYSTEM        Extra system prompt text (appended after ToS guardrail)
  TWITCH_CHATBOT_TYPE_PHRASES  Comma-separated phrases for "type …" (see secrets.sh)
  TWITCH_CHATBOT_TYPE_MAX      Max repeats per type command (default 10)
  TWITCH_CHATBOT_SHANKS_ROASTS Path to shanks_roasts.json (default beside script)

Usage:
  source ./secrets.sh && python3 twitch_chatbot.py
"""

from __future__ import annotations

import argparse
import json
import os
import random
import re
import sys
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import Future, ThreadPoolExecutor

from twitch_mirror import (
    DEFAULT_MIN_SEND_INTERVAL,
    TwitchChatSender,
    parse_twitch_privmsg,
    sanitize_irc_line,
)

TWITCH_MSG_MAX = 500
DEFAULT_COOLDOWN = 15.0
DEFAULT_TYPE_PHRASES = ("DrakeWide Juice", "aaronWiFi Juice")
DEFAULT_TYPE_MAX = 10
_TYPE_COUNT_RE = re.compile(r"(?i)^(\d+)\s*(?:times|x)?(?:\b|[,.]|$)")
DEFAULT_BASE_URL = "http://127.0.0.1:11434/v1"
DEFAULT_MODEL = "llama3.2"
OLLAMA_API_KEY_PLACEHOLDER = "ollama"
TWITCH_TOS_GUARDRAIL = (
    "You must follow Twitch Terms of Service and Community Guidelines at all times. "
    "Do not generate harassment, hate, slurs, threats, sexual content, glorification of "
    "violence or self-harm, illegal activity, cheating/exploit instructions, ban-evasion "
    "advice, spam or engagement manipulation, impersonation of Twitch staff or the "
    "streamer, or sharing private personal information. "
    "Do not encourage viewers to break Twitch rules or platform policies. "
    "If a request would violate these rules, refuse briefly and suggest a safe alternative "
    "when possible. Stay neutral, respectful, and family-friendly for a live stream chat."
)

DEFAULT_SYSTEM = (
    "You are a helpful Twitch chat bot. Answer clearly and briefly in plain text. "
    "Keep replies under 450 characters. No markdown headings or bullet lists unless asked. "
    + TWITCH_TOS_GUARDRAIL
)

# Short canned reply when input is clearly out of policy (skips LLM).
_TOS_REFUSAL_REPLY = (
    "I can't help with that — it isn't allowed under Twitch's rules. Ask something else!"
)

# Obvious policy-breaking asks (case-insensitive); keep patterns coarse to limit false positives.
_TOS_BLOCKED_QUESTION_RE = re.compile(
    r"(?i)\b("
    r"dox|doxx|swat|viewbot|follow\s*for\s*follow|f4f|sub4sub|"
    r"ban\s*evad|evade\s*ban|buy\s*followers|bot\s*followers|"
    r"how\s+to\s+(hack|cheat|ddos)|"
    r"slur|say\s+the\s+n\s*word"
    r")\b"
)

_SHANKS_NAME_RE = re.compile(r"(?i)\bshanks(?:_ttv)?\b")
_SHANKS_GAMING_INTENT_RE = re.compile(
    r"(?i)\b("
    r"good|bad|trash|garbage|mid|dogwater|ass|suck|sucks|worst|best|"
    r"skill|skilled|mechanics|aim|clutch|carry|feed|feeds|int|inting|"
    r"video\s*games?|gaming|gamer|play(?:er|ing|s)?|"
    r"roast|insult|diss|flame|trash\s*talk|"
    r"rank|elo|competitive|ranked|"
    r"rate|rating|opinion|"
    r"short|tall|height|ugly|handsome|look|looks|face|appearance"
    r")\b"
)

_QUOTE_WRAPPER_PAIRS = (
    ('"', '"'),
    ("'", "'"),
    ("\u201c", "\u201d"),  # “ ”
    ("\u2018", "\u2019"),  # ‘ ’
)

_SHANKS_ROASTS_DEFAULT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "shanks_roasts.json")
_shanks_roasts_cache: tuple[str, ...] | None = None


def load_shanks_roasts() -> tuple[str, ...]:
    """Load canned Shanks roasts from JSON (list of strings)."""
    global _shanks_roasts_cache
    if _shanks_roasts_cache is not None:
        return _shanks_roasts_cache
    path = os.environ.get("TWITCH_CHATBOT_SHANKS_ROASTS", _SHANKS_ROASTS_DEFAULT).strip()
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except OSError as e:
        raise RuntimeError(f"Cannot read Shanks roasts file {path!r}: {e}") from e
    except json.JSONDecodeError as e:
        raise RuntimeError(f"Invalid JSON in Shanks roasts file {path!r}: {e}") from e
    if not isinstance(data, list) or not data:
        raise RuntimeError(f"Shanks roasts file {path!r} must be a non-empty JSON array")
    roasts: list[str] = []
    for i, item in enumerate(data):
        if not isinstance(item, str) or not item.strip():
            raise RuntimeError(f"Shanks roasts file {path!r}: entry {i} must be a non-empty string")
        roasts.append(item.strip())
    _shanks_roasts_cache = tuple(roasts)
    return _shanks_roasts_cache


def pick_shanks_roast() -> str:
    return random.choice(load_shanks_roasts())


def is_shanks_gaming_question(question: str) -> bool:
    """True when the ask is about shanks/shanks_ttv (skill, looks, height, roasts)."""
    if not _SHANKS_NAME_RE.search(question):
        return False
    return bool(_SHANKS_GAMING_INTENT_RE.search(question))


def mentions_bot(body: str, bot_nick: str) -> bool:
    """True if the message @mentions the bot or starts with its login."""
    nick = bot_nick.lower()
    lower = body.lower()
    if re.search(rf"(?<!\w)@{re.escape(nick)}(?!\w)", lower):
        return True
    if re.match(rf"^{re.escape(nick)}(?!\w)", lower):
        return True
    return False


def extract_question(body: str, bot_nick: str) -> str | None:
    """Strip the mention and return the question, or None if empty."""
    if not mentions_bot(body, bot_nick):
        return None
    text = body.strip()
    text = re.sub(rf"^@?{re.escape(bot_nick)}\b[,:]?\s*", "", text, count=1, flags=re.IGNORECASE)
    text = re.sub(
        rf"\s*@?{re.escape(bot_nick)}\b[,:]?\s*$", "", text, count=1, flags=re.IGNORECASE
    )
    text = text.strip()
    return text or None


def truncate_for_twitch(text: str, limit: int = TWITCH_MSG_MAX) -> str:
    text = sanitize_irc_line(text)
    if len(text) <= limit:
        return text
    if limit < 2:
        return text[:limit]
    return text[: limit - 1].rstrip() + "…"


def parse_allowed_users(raw: str) -> frozenset[str]:
    """Parse comma/space-separated Twitch logins (lowercase)."""
    users: set[str] = set()
    for part in raw.replace(" ", ",").split(","):
        login = part.strip().lower()
        if login:
            users.add(login)
    return frozenset(users)


def resolve_allowed_users(cli_value: str) -> frozenset[str] | None:
    """None = any chatter in the channel; otherwise a whitelist of logins."""
    raw = (
        cli_value.strip()
        or os.environ.get("TWITCH_CHATBOT_ALLOWED_USERS", "").strip()
        or os.environ.get("TWITCH_CHATBOT_ALLOWED_USER", "").strip()
    )
    if not raw or raw.lower() in ("*", "all", "any", "everyone"):
        return None
    return parse_allowed_users(raw)


def question_blocked_by_tos(question: str) -> bool:
    """True when the ask is clearly against Twitch policy (skip LLM)."""
    return bool(_TOS_BLOCKED_QUESTION_RE.search(question))


def parse_type_phrases(raw: str) -> tuple[str, ...]:
    phrases: list[str] = []
    for part in raw.replace(";", ",").split(","):
        phrase = " ".join(part.split())
        if phrase:
            phrases.append(phrase)
    return tuple(phrases) if phrases else DEFAULT_TYPE_PHRASES


def resolve_type_phrases() -> tuple[str, ...]:
    raw = os.environ.get("TWITCH_CHATBOT_TYPE_PHRASES", "").strip()
    if not raw:
        return DEFAULT_TYPE_PHRASES
    return parse_type_phrases(raw)


def resolve_type_max() -> int:
    raw = os.environ.get("TWITCH_CHATBOT_TYPE_MAX", "").strip()
    if not raw:
        return DEFAULT_TYPE_MAX
    try:
        return max(1, min(int(raw), 25))
    except ValueError:
        return DEFAULT_TYPE_MAX


def parse_type_command(question: str, allowed: tuple[str, ...]) -> tuple[str, int] | None:
    """
    Parse 'type <whitelisted phrase>' with optional repeat count.
    Extra text after the command is allowed (e.g. '…5 times, exactly how I typed it').
    """
    text = " ".join(question.split())
    if not re.match(r"(?i)^type\s+", text):
        return None
    rest = re.sub(r"(?i)^type\s+", "", text, count=1).strip()
    for phrase in sorted(allowed, key=len, reverse=True):
        m = re.match(rf"^{re.escape(phrase)}", rest, re.IGNORECASE)
        if not m:
            continue
        after = rest[m.end() :].strip()
        count = 1
        cm = _TYPE_COUNT_RE.match(after)
        if cm:
            count = int(cm.group(1))
        return phrase, count
    return None


def strip_wrapping_quotes(text: str) -> str:
    """Remove outer quote pairs models often add around one-liner roasts."""
    result = text.strip()
    while len(result) >= 2:
        stripped = False
        for open_q, close_q in _QUOTE_WRAPPER_PAIRS:
            if result.startswith(open_q) and result.endswith(close_q):
                result = result[len(open_q) : -len(close_q)].strip()
                stripped = True
                break
        if not stripped:
            break
    return result


def format_chat_reply(asker: str, reply: str) -> str:
    """Build @mention reply capped at Twitch's 500-character limit."""
    prefix = f"@{asker} "
    max_body = TWITCH_MSG_MAX - len(prefix)
    body = truncate_for_twitch(reply, limit=max(1, max_body))
    return truncate_for_twitch(prefix + body, limit=TWITCH_MSG_MAX)


def ask_llm(
    *,
    question: str,
    asker: str,
    api_key: str,
    base_url: str,
    model: str,
    system_extra: str,
) -> str:
    system = DEFAULT_SYSTEM
    temperature = 0.7
    if system_extra.strip():
        system = system + " " + system_extra.strip()
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {
                "role": "user",
                "content": f"Twitch user '{asker}' asks: {question}",
            },
        ],
        "max_tokens": 350,
        "temperature": temperature,
    }
    url = base_url.rstrip("/") + "/chat/completions"
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"LLM HTTP {e.code}: {detail}") from e

    choices = data.get("choices")
    if not choices:
        raise RuntimeError("LLM returned no choices")
    message = choices[0].get("message") or {}
    content = message.get("content")
    if not isinstance(content, str) or not content.strip():
        raise RuntimeError("LLM returned empty content")
    return strip_wrapping_quotes(content.strip())


class ChatbotWorker:
    def __init__(
        self,
        sender: TwitchChatSender,
        *,
        bot_nick: str,
        api_key: str,
        base_url: str,
        model: str,
        system_extra: str,
        cooldown: float,
        type_phrases: tuple[str, ...],
        type_max: int,
    ) -> None:
        self.sender = sender
        self.bot_nick = bot_nick.lower()
        self.api_key = api_key
        self.base_url = base_url
        self.model = model
        self.system_extra = system_extra
        self.cooldown = cooldown
        self.type_phrases = type_phrases
        self.type_max = type_max
        self._cooldown_until: dict[str, float] = {}
        self._cooldown_lock = threading.Lock()
        self._executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="llm")
        self._pending: set[Future[object]] = set()
        self._pending_lock = threading.Lock()

    def enqueue(self, asker: str, question: str) -> None:
        asker_l = asker.lower()
        if asker_l == self.bot_nick:
            return

        with self._cooldown_lock:
            until = self._cooldown_until.get(asker_l, 0.0)
            if time.monotonic() < until:
                print(f"@{asker} on cooldown, skipping.", file=sys.stderr)
                return
            self._cooldown_until[asker_l] = time.monotonic() + self.cooldown

        print(f"@{asker}: {question}", file=sys.stderr)
        future = self._executor.submit(self._answer, asker, question)
        with self._pending_lock:
            self._pending.add(future)
            future.add_done_callback(self._done)

    def _done(self, fut: Future[object]) -> None:
        with self._pending_lock:
            self._pending.discard(fut)

    def _send_type_phrase(self, phrase: str, count: int) -> None:
        line = truncate_for_twitch(" ".join([phrase] * count))
        self.sender.send_chat(line)

    def _answer(self, asker: str, question: str) -> None:
        try:
            type_cmd = parse_type_command(question, self.type_phrases)
            if type_cmd is not None:
                phrase, count = type_cmd
                repeats = max(1, min(count, self.type_max))
                self._send_type_phrase(phrase, repeats)
                return
            shanks_roast = is_shanks_gaming_question(question)
            if question_blocked_by_tos(question) and not shanks_roast:
                self.sender.send_chat(format_chat_reply(asker, _TOS_REFUSAL_REPLY))
                return
            if shanks_roast:
                reply = pick_shanks_roast()
            else:
                reply = ask_llm(
                    question=question,
                    asker=asker,
                    api_key=self.api_key,
                    base_url=self.base_url,
                    model=self.model,
                    system_extra=self.system_extra,
                )
            out = format_chat_reply(asker, reply)
            if out:
                self.sender.send_chat(out)
        except Exception as e:  # noqa: BLE001 — report to chat
            print(f"LLM error for @{asker}: {e}", file=sys.stderr)
            self.sender.send_chat(
                format_chat_reply(asker, "sorry, I couldn't answer that right now.")
            )

    def shutdown(self) -> None:
        self._executor.shutdown(wait=False, cancel_futures=True)


def recv_chatbot_loop(
    sender: TwitchChatSender,
    *,
    bot_nick: str,
    allowed_users: frozenset[str] | None,
    worker: ChatbotWorker,
) -> None:
    if sender._sock is None:
        return
    chan_want = sender.channel.lower()
    buff = b""

    while True:
        chunk = sender._sock.recv(4096)
        if not chunk:
            print(f"Disconnected from Twitch IRC (NICK {sender.nick}).", file=sys.stderr)
            raise SystemExit(1)
        buff += chunk
        while b"\r\n" in buff:
            line, buff = buff.split(b"\r\n", 1)
            msg = line.decode("utf-8", errors="replace")
            if ":tmi.twitch.tv NOTICE" in msg:
                print(f"Twitch IRC [{sender.nick}]: {msg}", file=sys.stderr)
            if msg.startswith("PING "):
                payload = msg.split("PING ", 1)[1]
                sender._writeln(f"PONG :{payload}")
                continue
            parsed = parse_twitch_privmsg(msg)
            if parsed is None:
                continue
            login, chan, body = parsed
            if chan != chan_want:
                continue
            if login == bot_nick.lower():
                continue
            if allowed_users is not None and login not in allowed_users:
                continue
            question = extract_question(body, bot_nick)
            if question:
                worker.enqueue(login, question)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Twitch @mention chatbot powered by an LLM.")
    p.add_argument(
        "--min-interval",
        type=float,
        default=DEFAULT_MIN_SEND_INTERVAL,
        help=f"Minimum seconds between bot messages (default {DEFAULT_MIN_SEND_INTERVAL}).",
    )
    p.add_argument(
        "--cooldown",
        type=float,
        default=float(os.environ.get("TWITCH_CHATBOT_COOLDOWN", DEFAULT_COOLDOWN)),
        help=f"Per-user cooldown in seconds (default {DEFAULT_COOLDOWN:g}).",
    )
    p.add_argument(
        "--allowed-users",
        type=str,
        default="",
        help="Comma-separated logins, or * for everyone (TWITCH_CHATBOT_ALLOWED_USERS).",
    )
    return p.parse_args()


def resolve_credentials() -> tuple[str, str, str]:
    oauth = os.environ.get("TWITCH_CHATBOT_OAUTH", "").strip() or os.environ.get(
        "TWITCH_OAUTH", ""
    ).strip()
    nick = os.environ.get("TWITCH_CHATBOT_NICK", "").strip() or os.environ.get(
        "TWITCH_NICK", ""
    ).strip()
    channel = os.environ.get("TWITCH_CHANNEL", "").strip()
    return oauth, nick, channel


def main() -> int:
    args = parse_args()
    oauth, nick, channel = resolve_credentials()
    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    base_url = os.environ.get("OPENAI_BASE_URL", DEFAULT_BASE_URL).strip() or DEFAULT_BASE_URL
    model = os.environ.get("OPENAI_MODEL", DEFAULT_MODEL).strip() or DEFAULT_MODEL
    system_extra = os.environ.get("TWITCH_CHATBOT_SYSTEM", "").strip()

    if not oauth or not nick or not channel:
        print(
            "Set TWITCH_OAUTH, TWITCH_NICK, TWITCH_CHANNEL (or TWITCH_CHATBOT_*).",
            file=sys.stderr,
        )
        return 2
    allowed_users = resolve_allowed_users(args.allowed_users)
    using_ollama = "11434" in base_url or "ollama" in base_url.lower()
    if not api_key:
        if using_ollama:
            api_key = OLLAMA_API_KEY_PLACEHOLDER
        else:
            print("Set OPENAI_API_KEY (see secrets.sh).", file=sys.stderr)
            return 2

    sender = TwitchChatSender(
        nick=nick, oauth=oauth, channel=channel, min_interval=args.min_interval
    )
    sender.connect()

    type_phrases = resolve_type_phrases()
    type_max = resolve_type_max()

    worker = ChatbotWorker(
        sender,
        bot_nick=nick,
        api_key=api_key,
        base_url=base_url,
        model=model,
        system_extra=system_extra,
        cooldown=max(args.cooldown, 1.0),
        type_phrases=type_phrases,
        type_max=type_max,
    )

    llm_src = "Ollama" if using_ollama else base_url
    if allowed_users is None:
        allowed_desc = "all chatters"
    else:
        allowed_desc = ", ".join(f"@{u}" for u in sorted(allowed_users))
    type_list = ", ".join(repr(p) for p in type_phrases)
    print(
        f"Chatbot @{nick} live in #{channel}. Allowed askers: {allowed_desc}. "
        f"Type command: {type_list} (max {type_max}x). "
        f"Twitch ToS guardrail on. Shanks roasts: {len(load_shanks_roasts())} canned. "
        f"Mention @{nick} + question. "
        f"Replies max {TWITCH_MSG_MAX} chars. "
        f"LLM: {llm_src} / {model}. Ctrl+C to exit.",
        file=sys.stderr,
    )

    try:
        recv_chatbot_loop(
            sender,
            bot_nick=nick,
            allowed_users=allowed_users,
            worker=worker,
        )
    finally:
        worker.shutdown()
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\nExiting.", file=sys.stderr)
        raise SystemExit(130)
