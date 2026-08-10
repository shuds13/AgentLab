#!/bin/bash
# Post a message to Slack via an incoming webhook. OPTIONAL: if no webhook file is
# configured/present, or the post fails, this exits non-zero WITHOUT affecting the
# caller -- agent.py ignores the result, so the agent runs fine with no Slack access.
#
# Usage: slack_notify.sh "message text"
# Webhook file: $SLACK_WEBHOOK_FILE, else ~/.slack_webhook  (keep this file OUT of the repo)
set -u
WEBHOOK_FILE="${SLACK_WEBHOOK_FILE:-$HOME/.slack_webhook}"
MSG="$*"
if [ -z "$MSG" ]; then echo "usage: $0 <message>" >&2; exit 2; fi
if [ ! -s "$WEBHOOK_FILE" ]; then echo "slack_notify: no webhook file at $WEBHOOK_FILE (skipping)" >&2; exit 3; fi
WH="$(cat "$WEBHOOK_FILE")"
ESC="$(printf '%s' "$MSG" | python3 -c 'import json,sys; print(json.dumps(sys.stdin.read()))')"
CODE="$(curl -sS -m 20 -o /dev/null -w '%{http_code}' -X POST -H 'Content-type: application/json' --data "{\"text\": $ESC}" "$WH")"
if [ "$CODE" != "200" ]; then echo "slack_notify: post failed (http $CODE)" >&2; exit 1; fi
echo "slack_notify: sent (http 200)"
