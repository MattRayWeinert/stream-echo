# Stream Echo

Twitch chat utilities: mirror another chatter into your channel, multi-account send, a roast-style chatbot, and a Caps Lock + Enter Chrome helper for fast chat toggles.

## Setup

1. Copy `secrets.example.sh` → `secrets.sh` and fill in your Twitch OAuth tokens (`chat:read` + `chat:edit`).
2. Source secrets and run:

```bash
source ./secrets.sh
python3 twitch_mirror.py
```

Generate tokens with:

```bash
# Set TWITCH_CLIENT_ID / TWITCH_CLIENT_SECRET / TWITCH_REDIRECT_URI first
python3 twitch_oauth_code_flow.py
```

## Scripts

| Script | Purpose |
|--------|---------|
| `twitch_mirror.py` | Mirror another user (and/or stdin) into chat; optional multi-account fan-out |
| `twitch_chatbot.py` | LLM-backed chat bot |
| `twitch_swap_mirror.py` | Mirror only on exact token/phrase matches |
| `twitch_after_two_messages.py` | Triggered replies after message patterns |
| `caps-enter-extension/` | Chrome extension + native host for Caps Lock chat mode |

## Notes

- Sends **one chat message per line** (not per keystroke) to stay within Twitch norms.
- Enforces a minimum delay between outbound messages per account.
- Keep `secrets.sh` local — never commit tokens.
