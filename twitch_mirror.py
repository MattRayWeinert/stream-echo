#!/usr/bin/env python3
"""
Mirror input to Twitch chat over IRC — optionally from stdin and/or another chatter.

Design choices (important):
- Sends one chat message per line (Enter from stdin, or one IRC line from the mirrored user).
  Per-keystroke mirroring would flood chat and violate Twitch norms.
- Enforces a minimum delay between outbound messages per account to reduce ban/ratelimit risk.

Requires env vars:
  TWITCH_OAUTH    OAuth token with chat:read + chat:edit (IRC send; prefix oauth: or we add it)
  TWITCH_NICK     Account username (lowercase login)
  TWITCH_CHANNEL  Channel to join (without leading #)

Extra accounts (same channel; each sends the same mirrored text — optional):
  TWITCH_OAUTH_2 / TWITCH_NICK_2    Second account
  TWITCH_OAUTH_3 / TWITCH_NICK_3    Third account
  TWITCH_OAUTH_4 / TWITCH_NICK_4    Fourth account
  For each pair, set both env vars or neither.

Mirror another user's chat into yours (needs chat:read + chat:edit on tokens used for IRC):
  TWITCH_MIRROR_USER   Their Twitch login (case-insensitive), or use --mirror-user
  With mirroring enabled, stdin is not read unless you pass --stdin (so EOF does not exit).

Usage:
  TWITCH_OAUTH=... TWITCH_NICK=a TWITCH_OAUTH_2=... TWITCH_NICK_2=b \\
    TWITCH_OAUTH_3=... TWITCH_NICK_3=c TWITCH_OAUTH_4=... TWITCH_NICK_4=d \\
    TWITCH_CHANNEL=chan TWITCH_MIRROR_USER=theirlogin python3 twitch_mirror.py

Optional:
  --repeat-seconds N   Re-send the last line every N seconds (minimum 30).
  --stdin             Read lines from stdin as well (when using --mirror-user / TWITCH_MIRROR_USER).
"""

from __future__ import annotations

import argparse
import os
import queue
import ssl
import sys
import threading
import time


TWITCH_HOST = "irc.chat.twitch.tv"
TWITCH_PORT = 6697

# Twitch broadly expects ~20 messages / 30s for normal accounts in one channel.
DEFAULT_MIN_SEND_INTERVAL = 1.6
MIN_REPEAT_INTERVAL = 30.0


def normalize_oauth(raw: str) -> str:
    raw = raw.strip()
    if not raw.startswith("oauth:"):
        return "oauth:" + raw.lstrip("oauth:").lstrip(":")
    return raw


def sanitize_irc_line(text: str) -> str:
    # Twitch chat messages should not contain CR/LF/NUL.
    text = text.replace("\r", " ").replace("\n", " ").replace("\x00", "")
    return text.strip()


def parse_twitch_privmsg(line: str) -> tuple[str, str, str] | None:
    """Parse a Twitch IRC PRIVMSG; return (login_lower, channel_lower, body) or None."""
    s = line.strip("\r\n")
    if s.startswith("@"):
        sp = s.find(" ")
        if sp == -1:
            return None
        s = s[sp + 1 :].lstrip()
    if not s.startswith(":"):
        return None
    try:
        prefix_end = s.index(" ", 1)
        prefix = s[1:prefix_end]
        rest = s[prefix_end + 1 :]
        nick = prefix.split("!", 1)[0].lower()
        if not rest.startswith("PRIVMSG "):
            return None
        rest = rest[len("PRIVMSG ") :]
        if not rest.startswith("#"):
            return None
        sp = rest.index(" ")
        chan = rest[1:sp].lower()
        trailer = rest[sp + 1 :]
        if not trailer.startswith(":"):
            return None
        body = trailer[1:]
        return nick, chan, body
    except (ValueError, IndexError):
        return None


class TwitchChatSender:
    def __init__(
        self,
        nick: str,
        oauth: str,
        channel: str,
        min_interval: float,
    ) -> None:
        self.nick = nick.lower().strip()
        self.oauth = normalize_oauth(oauth)
        self.channel = channel.lower().lstrip("#").strip()
        self.min_interval = min_interval
        self._sock: ssl.SSLSocket | None = None
        self._send_lock = threading.Lock()
        self._last_send = 0.0

    def connect(self) -> None:
        ctx = ssl.create_default_context()
        raw = ssl.create_connection((TWITCH_HOST, TWITCH_PORT))
        sock = ctx.wrap_socket(raw, server_hostname=TWITCH_HOST)
        self._sock = sock

        self._writeln(f"PASS {self.oauth}")
        self._writeln(f"NICK {self.nick}")
        self._writeln("CAP REQ :twitch.tv/commands twitch.tv/tags")
        self._writeln(f"JOIN #{self.channel}")

    def _writeln(self, line: str) -> None:
        if self._sock is None:
            raise RuntimeError("not connected")
        data = (line + "\r\n").encode("utf-8", errors="replace")
        self._sock.sendall(data)

    def recv_loop(
        self,
        *,
        mirror_user: str | None,
        mirror_queue: queue.Queue[str] | None,
        ignore_nicks_lower: frozenset[str],
    ) -> None:
        if self._sock is None:
            return
        mu = mirror_user.lower().strip() if mirror_user else None
        buff = b""
        while True:
            chunk = self._sock.recv(4096)
            if not chunk:
                print(f"Disconnected from Twitch IRC (socket for NICK {self.nick}).", file=sys.stderr)
                os._exit(1)
            buff += chunk
            while b"\r\n" in buff:
                line, buff = buff.split(b"\r\n", 1)
                msg = line.decode("utf-8", errors="replace")
                if ":tmi.twitch.tv NOTICE" in msg:
                    print(f"Twitch IRC [{self.nick}]: {msg}", file=sys.stderr)
                if msg.startswith("PING "):
                    payload = msg.split("PING ", 1)[1]
                    self._writeln(f"PONG :{payload}")
                    continue
                if mu and mirror_queue is not None:
                    parsed = parse_twitch_privmsg(msg)
                    if parsed is None:
                        continue
                    login, chan, body = parsed
                    if chan != self.channel or login != mu:
                        continue
                    if login in ignore_nicks_lower:
                        continue
                    payload = sanitize_irc_line(body)
                    if payload:
                        mirror_queue.put(payload)

    def send_chat(self, text: str) -> None:
        text = sanitize_irc_line(text)
        if not text:
            return
        with self._send_lock:
            now = time.monotonic()
            wait = self.min_interval - (now - self._last_send)
            if wait > 0:
                time.sleep(wait)
            self._writeln(f"PRIVMSG #{self.channel} :{text}")
            self._last_send = time.monotonic()


def reader_thread(q: queue.Queue[str]) -> None:
    try:
        for line in sys.stdin:
            q.put(line.rstrip("\r\n"))
    except EOFError:
        pass
    finally:
        q.put("")  # sentinel


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Mirror stdin and/or a Twitch user to chat (up to four accounts).")
    p.add_argument(
        "--min-interval",
        type=float,
        default=DEFAULT_MIN_SEND_INTERVAL,
        help=f"Minimum seconds between messages per account (default {DEFAULT_MIN_SEND_INTERVAL}).",
    )
    p.add_argument(
        "--repeat-seconds",
        type=float,
        default=0.0,
        help=f"If set, repeat the last non-empty line every N seconds (min {MIN_REPEAT_INTERVAL:g}).",
    )
    p.add_argument(
        "--mirror-user",
        type=str,
        default="",
        help="Twitch login to mirror from chat (also TWITCH_MIRROR_USER). Enables IRC capture on first connection.",
    )
    p.add_argument(
        "--stdin",
        action="store_true",
        help="Read stdin as well. Default on without --mirror-user; with mirror, stdin is off unless this flag is set.",
    )
    return p.parse_args()


def send_all(senders: list[TwitchChatSender], text: str) -> None:
    for s in senders:
        s.send_chat(text)


def main() -> int:
    args = parse_args()
    oauth = os.environ.get("TWITCH_OAUTH", "").strip()
    nick = os.environ.get("TWITCH_NICK", "").strip()
    channel = os.environ.get("TWITCH_CHANNEL", "").strip()
    if not oauth or not nick or not channel:
        print(
            "Set TWITCH_OAUTH, TWITCH_NICK, TWITCH_CHANNEL (see script docstring).",
            file=sys.stderr,
        )
        return 2

    optional_pairs = (
        ("TWITCH_OAUTH_2", "TWITCH_NICK_2"),
        ("TWITCH_OAUTH_3", "TWITCH_NICK_3"),
        ("TWITCH_OAUTH_4", "TWITCH_NICK_4"),
    )
    extra_accounts: list[tuple[str, str]] = []
    for oauth_key, nick_key in optional_pairs:
        o = os.environ.get(oauth_key, "").strip()
        n = os.environ.get(nick_key, "").strip()
        if bool(o) != bool(n):
            print(f"Set both {oauth_key} and {nick_key}, or neither.", file=sys.stderr)
            return 2
        if o and n:
            extra_accounts.append((o, n))

    mirror_raw = (args.mirror_user or os.environ.get("TWITCH_MIRROR_USER", "")).strip()
    mirror_user = mirror_raw.lower() if mirror_raw else None
    use_stdin = (not mirror_user) or args.stdin

    repeat = float(args.repeat_seconds or 0.0)
    if repeat and repeat < MIN_REPEAT_INTERVAL:
        print(
            f"--repeat-seconds must be >= {MIN_REPEAT_INTERVAL:g} (refusing faster loops).",
            file=sys.stderr,
        )
        return 2

    senders: list[TwitchChatSender] = [
        TwitchChatSender(nick=nick, oauth=oauth, channel=channel, min_interval=args.min_interval),
    ]
    senders[0].connect()

    for eo, en in extra_accounts:
        s = TwitchChatSender(nick=en, oauth=eo, channel=channel, min_interval=args.min_interval)
        senders.append(s)
        s.connect()

    ignore = frozenset(s.nick for s in senders)

    q: queue.Queue[str] = queue.Queue()

    threading.Thread(
        target=senders[0].recv_loop,
        kwargs={
            "mirror_user": mirror_user,
            "mirror_queue": q if mirror_user else None,
            "ignore_nicks_lower": ignore,
        },
        daemon=True,
    ).start()

    for extra in senders[1:]:
        threading.Thread(
            target=extra.recv_loop,
            kwargs={
                "mirror_user": None,
                "mirror_queue": None,
                "ignore_nicks_lower": ignore,
            },
            daemon=True,
        ).start()

    if use_stdin:
        threading.Thread(target=reader_thread, args=(q,), daemon=True).start()

    last_payload: str | None = None

    def repeat_worker() -> None:
        nonlocal last_payload
        while True:
            time.sleep(repeat)
            if last_payload:
                try:
                    send_all(senders, last_payload)
                except BrokenPipeError:
                    return

    if repeat > 0:
        threading.Thread(target=repeat_worker, daemon=True).start()

    parts = [f"{s.nick}" for s in senders]
    acct = " + ".join(parts)
    src = (
        f"mirroring @{mirror_user}"
        if mirror_user
        else "stdin"
    )
    if mirror_user and use_stdin:
        src += " + stdin"
    print(
        f"Joining IRC as {acct} → #{channel} ({src}). "
        "If you see 'Disconnected' immediately, check NOTICE lines above "
        "(token, scopes chat:read+chat:edit, nick matches token user; any bad extra account drops all connections). "
        + ("Ctrl+D exits." if use_stdin else "Ctrl+C to exit."),
        file=sys.stderr,
    )

    while True:
        line = q.get()
        if line == "":
            break
        if line.strip() == "":
            continue
        last_payload = sanitize_irc_line(line)
        if not last_payload:
            continue
        send_all(senders, last_payload)

    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\nExiting (Ctrl+C).", file=sys.stderr)
        raise SystemExit(130)
