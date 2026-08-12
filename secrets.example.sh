#!/usr/bin/env bash
# Copy to secrets.sh, fill in your values, then:
#   source ./secrets.sh && python3 twitch_mirror.py
#
# Do not commit secrets.sh — it is gitignored.

unset TWITCH_OAUTH_2 TWITCH_NICK_2 TWITCH_OAUTH_3 TWITCH_NICK_3 TWITCH_OAUTH_4 TWITCH_NICK_4
unset TWITCH_PHRASE_OAUTH TWITCH_PHRASE_NICK
unset TWITCH_OAUTH_USE_DEFAULT_BROWSER

export TWITCH_CHANNEL='your_channel'

# Primary IRC account (chat:read + chat:edit)
export TWITCH_OAUTH='oauth:YOUR_ACCESS_TOKEN'
export TWITCH_NICK='your_bot_login'

# Optional: mirror another chatter into your channel
# export TWITCH_MIRROR_USER='their_login'

# Optional: chatbot (OpenAI-compatible)
# export TWITCH_CHATBOT_OAUTH='oauth:YOUR_ACCESS_TOKEN'
# export TWITCH_CHATBOT_NICK='your_bot_login'
# export OPENAI_API_KEY='sk-...'
