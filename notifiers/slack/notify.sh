#!/bin/bash
# Slack's outbound half: POST one message to the lab's incoming webhook. Called by
# framework/notify.sh, which has already recorded the message, so anything that goes
# wrong here costs the post and nothing else.
#
# Usage: notify.sh "message text"
#
# SLACK_WEBHOOK_FILE  file holding the webhook URL, named in this lab's slack.env. The
#                     URL is the credential and the channel both, so a lab that names
#                     no file has no Slack and this exits without posting.
# NOTIFY_PREFIX       who is speaking, rendered as Slack bold. Several posters share one
#                     channel -- each campaign's agent, the secretary, the engineer --
#                     and without it their messages are indistinguishable.
set -u
WEBHOOK_FILE="${SLACK_WEBHOOK_FILE:-}"
MSG="$*"
if [ -z "$MSG" ]; then echo "usage: $0 <message>" >&2; exit 2; fi
[ -n "${NOTIFY_PREFIX:-}" ] && MSG="*[${NOTIFY_PREFIX}]* ${MSG}"

if [ -z "$WEBHOOK_FILE" ]; then echo "slack: no SLACK_WEBHOOK_FILE in notifiers/slack/slack.env; this lab has no Slack (skipping)" >&2; exit 3; fi
if [ ! -s "$WEBHOOK_FILE" ]; then echo "slack: no webhook file at $WEBHOOK_FILE (skipping)" >&2; exit 3; fi
WH="$(cat "$WEBHOOK_FILE")"
ESC="$(printf '%s' "$MSG" | python3 -c 'import json,sys; print(json.dumps(sys.stdin.read()))')"
CODE="$(curl -sS -m 20 -o /dev/null -w '%{http_code}' -X POST -H 'Content-type: application/json' --data "{\"text\": $ESC}" "$WH")"
if [ "$CODE" != "200" ]; then echo "slack: post failed (http $CODE)" >&2; exit 1; fi
echo "slack: sent (http 200)"
