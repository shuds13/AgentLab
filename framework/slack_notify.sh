#!/bin/bash
# Post a message to the lab: to the transcript always, and to Slack when a webhook is
# configured. OPTIONAL: if no webhook file is configured/present, or the post fails,
# this exits non-zero WITHOUT affecting the caller -- agent.py ignores the result, so
# the agent runs fine with no Slack access.
#
# The transcript write comes first and is unconditional, so a lab with no Slack still
# has somewhere for a reply to land: the secretary is told to answer by running this
# script, and without it its answers would go nowhere. $WORKSPACE_ROOT/run/MESSAGES.md
# is the lab-level counterpart of a campaign's own MESSAGES.md, and is what the watch
# page reads.
#
# Usage: slack_notify.sh "message text"
# Webhook file: $SLACK_WEBHOOK_FILE, else ~/.slack_webhook  (keep this file OUT of the repo)
#
# $SLACK_PREFIX (e.g. "local-test/local-both", or "secretary") is prepended to every
# message. It is applied HERE rather than by the callers because several posters share
# one channel -- each campaign's agent via tools.py, the secretary by running this
# script directly -- and without it their messages are indistinguishable.
set -u
WEBHOOK_FILE="${SLACK_WEBHOOK_FILE:-$HOME/.slack_webhook}"
MSG="$*"
[ -n "${SLACK_PREFIX:-}" ] && MSG="*[${SLACK_PREFIX}]* ${MSG}"
if [ -z "$MSG" ]; then echo "usage: $0 <message>" >&2; exit 2; fi

# The transcript, before any network call: a failed or unconfigured post must not lose
# the message. Best-effort -- an unwritable workspace is not a reason to fail the post.
LAB_RUN="${WORKSPACE_ROOT:-$(dirname "$0")/../workspace}/run"
if mkdir -p "$LAB_RUN" 2>/dev/null; then
    printf '`%s` **%s** %s\n\n' "$(date +%H:%M)" "${SLACK_PREFIX:-lab}" "$*" \
        >> "$LAB_RUN/MESSAGES.md" 2>/dev/null || true
fi

if [ ! -s "$WEBHOOK_FILE" ]; then echo "slack_notify: no webhook file at $WEBHOOK_FILE (skipping)" >&2; exit 3; fi
WH="$(cat "$WEBHOOK_FILE")"
ESC="$(printf '%s' "$MSG" | python3 -c 'import json,sys; print(json.dumps(sys.stdin.read()))')"
CODE="$(curl -sS -m 20 -o /dev/null -w '%{http_code}' -X POST -H 'Content-type: application/json' --data "{\"text\": $ESC}" "$WH")"
if [ "$CODE" != "200" ]; then echo "slack_notify: post failed (http $CODE)" >&2; exit 1; fi
echo "slack_notify: sent (http 200)"
