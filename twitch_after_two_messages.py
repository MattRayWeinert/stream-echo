#!/usr/bin/env python3
"""
Single Twitch account: after every N chat messages from other people in the channel,
send your fixed phrase once — alternating ALL CAPS vs all lowercase each time.

Uses credentials from the environment after `source ./secrets.sh`:

  TWITCH_CHANNEL                     Required — which chat to watch.

  Either:
    TWITCH_OAUTH + TWITCH_NICK       Default account for this script.
  Or (only for this script — mirror bot unchanged):
    TWITCH_PHRASE_OAUTH + TWITCH_PHRASE_NICK   Override oauth/login here.

If TWITCH_PHRASE_* are unset, TWITCH_OAUTH / TWITCH_NICK are used.

  source ./secrets.sh
  python3 twitch_after_two_messages.py --message "your phrase here"

Or:

  export TWITCH_BOT_MESSAGE='your phrase'
  python3 twitch_after_two_messages.py

Requires: chat:read + chat:edit on the token you use.
"""

from __future__ import annotations

import argparse
import os
import sys

from twitch_mirror import (
    DEFAULT_MIN_SEND_INTERVAL,
    TwitchChatSender,
    parse_twitch_privmsg,
    sanitize_irc_line,
)


def recv_and_trigger(
    sender: TwitchChatSender,
    *,
    base_phrase: str,
    every_n: int,
) -> None:
    """Read IRC; after every `every_n` PRIVMSG lines from others in channel, send phrase."""
    if sender._sock is None:
        return
    my_login = sender.nick.lower()
    chan_want = sender.channel.lower()
    buff = b""
    seen = 0
    uppercase_next = True

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
            login, chan, _body = parsed
            if chan != chan_want:
                continue
            if login == my_login:
                continue
            seen += 1
            if seen >= every_n:
                seen = 0
                variant = base_phrase.upper() if uppercase_next else base_phrase.lower()
                uppercase_next = not uppercase_next
                sender.send_chat(variant)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Send a phrase after every N messages from others (alternating case).",
    )
    p.add_argument(
        "-m",
        "--message",
        default=os.environ.get("TWITCH_BOT_MESSAGE", "").strip(),
        help="Text to send (or set TWITCH_BOT_MESSAGE).",
    )
    p.add_argument(
        "-n",
        "--every-n",
        type=int,
        default=2,
        help="Trigger after this many chat messages from others (default 2).",
    )
    p.add_argument(
        "--min-interval",
        type=float,
        default=DEFAULT_MIN_SEND_INTERVAL,
        help=f"Minimum seconds between outgoing messages (default {DEFAULT_MIN_SEND_INTERVAL}).",
    )
    return p.parse_args()


def main() -> int:
    args = parse_args()
    channel = os.environ.get("TWITCH_CHANNEL", "").strip()

    phrase_oauth = os.environ.get("TWITCH_PHRASE_OAUTH", "").strip()
    phrase_nick = os.environ.get("TWITCH_PHRASE_NICK", "").strip()
    if bool(phrase_oauth) != bool(phrase_nick):
        print(
            "Set both TWITCH_PHRASE_OAUTH and TWITCH_PHRASE_NICK, or neither.",
            file=sys.stderr,
        )
        return 2
    if phrase_oauth and phrase_nick:
        oauth = phrase_oauth
        nick = phrase_nick
        acct_src = "TWITCH_PHRASE_*"
    else:
        oauth = os.environ.get("TWITCH_OAUTH", "").strip()
        nick = os.environ.get("TWITCH_NICK", "").strip()
        acct_src = "TWITCH_OAUTH / TWITCH_NICK"

    if not oauth or not nick or not channel:
        print(
            "Set TWITCH_CHANNEL and either (TWITCH_OAUTH + TWITCH_NICK) or "
            "(TWITCH_PHRASE_OAUTH + TWITCH_PHRASE_NICK). See script docstring.",
            file=sys.stderr,
        )
        return 2

    phrase = sanitize_irc_line(args.message)
    if not phrase:
        print("Provide non-empty --message or TWITCH_BOT_MESSAGE.", file=sys.stderr)
        return 2

    n = int(args.every_n)
    if n < 1:
        print("--every-n must be >= 1.", file=sys.stderr)
        return 2

    sender = TwitchChatSender(nick=nick, oauth=oauth, channel=channel, min_interval=args.min_interval)
    sender.connect()

    print(
        f"Watching #{channel} as {nick} ({acct_src}): after {n} message(s) from others, "
        f"sending alternating UPPER/lower: {phrase[:80]}{'…' if len(phrase) > 80 else ''}\n"
        "Ctrl+C to exit.",
        file=sys.stderr,
    )

    recv_and_trigger(sender, base_phrase=phrase, every_n=n)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\nExiting.", file=sys.stderr)
        raise SystemExit(130)
