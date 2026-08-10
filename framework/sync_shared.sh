#!/bin/bash
# OPTIONAL. Sync the shared directory between machines with Globus Transfer (no ssh).
#
# Only needed if the agent host and the compute system do not see the same
# filesystem, or if you want to publish results to collaborators. If everything is
# on one filesystem, you do not need this script.
#
# FILL IN the collection IDs and paths below -- the values here are placeholders.
# One-time setup (Globus Connect Personal + `globus login`) is in
# Globus Transfer, if the workspace is shared across machines.
#
# Usage:
#   ./sync_shared.sh          # push the shared dir to the mirror
#   ./sync_shared.sh -n       # do not wait for the push; leave it running on Globus
set -euo pipefail

export PATH="$HOME/.local/bin:$PATH"   # globus CLI often lives here, not on the default PATH

WAIT_FOR_PUSH=1          # -n leaves the push running on Globus instead of waiting
while [ "$#" -gt 0 ]; do
  case "$1" in
    -n|--no-wait) WAIT_FOR_PUSH=0 ;;
    *) echo "usage: $0 [-n|--no-wait]" >&2; exit 2 ;;
  esac
  shift
done

# --- collection IDs (from your Globus setup) ---
LOCAL_EP="00000000-0000-0000-0000-000000000000"    # the agent host (Globus Connect Personal)
COMPUTE_EP="00000000-0000-0000-0000-000000000000"  # the HPC filesystem collection
MIRROR_EP="$COMPUTE_EP"                            # where collaborators read from

# --- paths (Globus paths are relative to the collection ROOT, not POSIX paths) ---
LOCAL_SHARED="/path/to/shared/"                    # this workflow's WORKSPACE_DIR
MIRROR="/myproject/mirror/shared/"

# Each transfer is handed to the Globus service, which moves the data itself and keeps
# going regardless of this script. So only block on a transfer when a later step
# depends on it finishing.
#
# NOTE: the globus CLI prints its errors (e.g. "session reauthentication required") on
# STDOUT, so capturing stdout for the task id hides them. Everything captured is echoed
# back on failure -- otherwise the script dies with no explanation at all.
run_transfer() {   # wait|nowait label src dst [extra globus args...]
  local mode="$1" label="$2" src="$3" dst="$4"
  shift 4
  echo "$label"
  echo "    from $src"
  echo "    to   $dst"
  echo "    submitting to Globus ..."
  local out status=0
  out=$(globus transfer "$src" "$dst" --recursive --sync-level mtime \
    --label "$label" --format unix --jmespath 'task_id' "$@") || status=$?
  if [ "$status" -ne 0 ]; then
    echo "    TRANSFER NOT SUBMITTED (globus exit $status):" >&2
    printf '      %s\n' "$out" >&2
    exit "$status"
  fi
  echo "    submitted as task $out"
  if [ "$mode" = "wait" ]; then
    echo "    waiting for it to finish (ctrl-c stops the wait, not the transfer) ..."
    globus task wait "$out"
    echo "    complete."
  else
    echo "    Globus is transferring it; this script does not wait."
    echo "    check it with:  globus task show $out"
  fi
}

# 1) compute -> local. LEFT COMMENTED OUT: what a workflow needs pulled back, if
#    anything, is specific to that workflow, so there is no sensible default.
#
#    Before enabling it, know what --sync-level mtime does. It is level 2, and the
#    levels nest, so a destination file is overwritten if ANY of these hold: it is
#    missing, ITS SIZE DIFFERS, or its timestamp is older than the source's. "The
#    local copy is newer" therefore does NOT protect it -- a differing size
#    overwrites it regardless of timestamp. So only pull paths that no local agent
#    writes: the remote must be the sole writer, making the overwrite intended.
#
#    A pull must also finish BEFORE the push, or the mirror ships stale data, which
#    is why it is waited on even when -n is given.
#
# COMPUTE_OUTPUT="/myproject/runs/outputs/"        # what the remote jobs write
# LOCAL_OUTPUT="${LOCAL_SHARED}outputs/"           # where it lands locally
# run_transfer wait "pull outputs (compute -> local)" \
#   "$COMPUTE_EP:$COMPUTE_OUTPUT" "$LOCAL_EP:$LOCAL_OUTPUT"

# 2) local -> mirror: publish the shared dir. Recursive, so anything a run writes
#    under it (including runs/<run_id>/) is mirrored automatically. Nothing follows
#    it, so -n can leave it running on the Globus service instead of waiting.
push_mode=wait
[ "$WAIT_FOR_PUSH" = "0" ] && push_mode=nowait
run_transfer "$push_mode" "push shared dir (local -> mirror)" \
  "$LOCAL_EP:$LOCAL_SHARED" "$MIRROR_EP:$MIRROR"

echo "Submitted. Recent transfers:  globus task list --limit 5"
