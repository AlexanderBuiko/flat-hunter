#!/bin/sh
# Start the three tiers in one container and keep the bot in the foreground as PID 1.
#
# Wiring (all localhost, only the gateway's $PORT is bound outward for health):
#   bot --FLAT_HUNTER_GATEWAY_URL--> gateway --JARVIS_LLM_CORE_URL--> llm-core --> provider
#
# JARVIS_LLM_GATEWAY_URL is deliberately never set here: the llm-core must reach the
# provider directly. Pointing it back at the gateway (which forwards to the core) loops.
set -eu

: "${PORT:=8900}"

# [3] LLM core — private, provider calls + real cost accounting.
export JARVIS_LLM_CORE_HOST=127.0.0.1
export JARVIS_LLM_CORE_PORT=8901
python -m jarvis.serve &

# [2] Guarded gateway — binds $PORT (health + guarded proxy), forwards to the core.
export FLAT_HUNTER_GATEWAY_HOST=0.0.0.0
export FLAT_HUNTER_GATEWAY_PORT="$PORT"
export JARVIS_LLM_CORE_URL="http://127.0.0.1:8901"
python -m flat_hunter.gateway &

# Give the core/gateway a moment to bind before the first user message arrives.
sleep 2

# [1] Telegram bot — long-poll, routes every model call through the gateway.
export FLAT_HUNTER_GATEWAY_URL="http://127.0.0.1:${PORT}"
exec python -m flat_hunter bot
