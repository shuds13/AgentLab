#!/bin/bash
# Post a message from the lab: to the transcript always, and onward to a transport if
# this lab has one. The transport is optional -- a lab with no notifier configured
# still records everything said, which is what the watch page reads.
#
# The transcript write comes first and is unconditional, so a failed or unconfigured
# post cannot lose a message: the secretary is told to answer by running this script,
# and without it its answers would go nowhere. $WORKSPACE_ROOT/run/MESSAGES.md is the
# lab-level counterpart of a campaign's own MESSAGES.md.
#
# Usage: notify.sh "message text"
#
# The transport is notifiers/$NOTIFIER/notify.sh, default slack, and $NOTIFY_SCRIPT
# names another outright. It is handed the raw message and $NOTIFY_PREFIX, and formats
# both as its medium wants. A transport that is not configured exits 3 and this exits 0:
# the message is recorded either way, and a lab with no Slack has not failed to post.
# Any other non-zero is a configured transport that failed, and is passed on.
#
# $NOTIFY_PREFIX (e.g. "local-test/local-both", or "secretary") is who is speaking. It
# is set here rather than by the callers because several posters share one lab -- each
# campaign's agent via tools.py, the secretary by running this script directly -- and
# without it their messages are indistinguishable.
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
MSG="$*"
if [ -z "$MSG" ]; then echo "usage: $0 <message>" >&2; exit 2; fi
PREFIX="${NOTIFY_PREFIX:-${SLACK_PREFIX:-}}"

LAB_RUN="${WORKSPACE_ROOT:-$HERE/../workspace}/run"
if mkdir -p "$LAB_RUN" 2>/dev/null; then
    printf '`%s` **%s** %s\n\n' "$(date +%H:%M)" "${PREFIX:-lab}" "$MSG" \
        >> "$LAB_RUN/MESSAGES.md" 2>/dev/null || true
fi

TRANSPORT="${NOTIFY_SCRIPT:-$HERE/../notifiers/${NOTIFIER:-slack}/notify.sh}"
if [ ! -f "$TRANSPORT" ]; then
    echo "notify: no transport at $TRANSPORT; recorded in the transcript only" >&2
    exit 0
fi
NOTIFY_PREFIX="$PREFIX" bash "$TRANSPORT" "$MSG"
rc=$?
# 3 is a transport with nothing configured to carry the message. That is a lab without
# Slack, not a failed post: the transcript has the message, so the caller succeeded.
[ "$rc" = 3 ] && rc=0
exit "$rc"
