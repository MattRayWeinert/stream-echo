#!/usr/bin/env python3
"""
When one specific Twitch user chats, react only if their whole message is exactly
d, u, l, r, or sl (case-insensitive). Embedded letters do not count (e.g. "R how are u" is ignored).

Swaps (whole message must match exactly):
  d or D → u or U,   u or U → d or D
  l or L → r or R,   r or R → l or L
  sl → b, then b (two messages)

Requires (after `source ./secrets.sh`):
  TWITCH_OAUTH, TWITCH_NICK, TWITCH_CHANNEL
  TWITCH_SWAP_MIRROR_USER or --mirror-user

Usage:
  source ./secrets.sh && python3 twitch_swap_mirror.py
  source ./secrets.sh && python3 twitch_swap_mirror.py --mirror-user weo_fr
"""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Iterator

from twitch_mirror import (
    DEFAULT_MIN_SEND_INTERVAL,
    TwitchChatSender,
    parse_twitch_privmsg,
    sanitize_irc_line,
)

# Exact whole message (including case) → opposite letter(s) to send.
_SINGLE_OUT: dict[str, str] = {
    "d": "u",
    "D": "U",
    "u": "d",
    "U": "D",
    "l": "r",
    "L": "R",
    "r": "l",
    "R": "L",
}


def iter_swap_messages(text: str) -> Iterator[str]:
    """Yield chat messages only when the full line exactly matches a swap token."""
    if text in _SINGLE_OUT:
        yield _SINGLE_OUT[text]
        return
    if text.lower() == "sl":
        yield "b"
        yield "b"


def recv_mirror_and_send(
    sender: TwitchChatSender,
    *,
    mirror_user: str,
) -> None:
    if sender._sock is None:
        return
    target = mirror_user.lower().strip()
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
            # Only the targeted user — ignore all other chatters.
            if chan != chan_want or login != target:
                continue
            raw = sanitize_irc_line(body)
            for swap_msg in iter_swap_messages(raw):
                sender.send_chat(swap_msg)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Swap keys only for one Twitch user's messages; ignore everyone else.",
    )
    p.add_argument(
        "--mirror-user",
        type=str,
        default="",
        help="Twitch login to watch (overrides TWITCH_SWAP_MIRROR_USER).",
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
    oauth = os.environ.get("TWITCH_OAUTH", "").strip()
    nick = os.environ.get("TWITCH_NICK", "").strip()
    channel = os.environ.get("TWITCH_CHANNEL", "").strip()
    mirror_raw = (args.mirror_user or os.environ.get("TWITCH_SWAP_MIRROR_USER", "")).strip()

    if not oauth or not nick or not channel:
        print(
            "Set TWITCH_OAUTH, TWITCH_NICK, and TWITCH_CHANNEL (see script docstring).",
            file=sys.stderr,
        )
        return 2
    if not mirror_raw:
        print(
            "Set TWITCH_SWAP_MIRROR_USER or pass --mirror-user (one target login only).",
            file=sys.stderr,
        )
        return 2

    mirror_user = mirror_raw.lower()
    if mirror_user == nick.lower():
        print(
            "Target user cannot be the same as TWITCH_NICK (would echo your own swapped messages).",
            file=sys.stderr,
        )
        return 2
    sender = TwitchChatSender(
        nick=nick, oauth=oauth, channel=channel, min_interval=args.min_interval
    )
    sender.connect()

    print(
        f"Joining #{channel} as {nick}. @{mirror_user}: exact d/u/l/r/sl only "
        "(d↔u, l↔r; sl→b then b). Ctrl+C to exit.",
        file=sys.stderr,
    )

    recv_mirror_and_send(sender, mirror_user=mirror_user)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\nExiting.", file=sys.stderr)
        raise SystemExit(130)
