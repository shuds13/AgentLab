#!/usr/bin/env python3
"""A Claude Code PreToolUse hook: refuse a file write or edit outside one directory.

Usage (from a settings file): fence_writes.py <directory>

Reads the tool call as JSON on stdin. A path outside the directory, after resolving
symlinks, is refused with exit code 2 and the reason on stderr, which Claude sees.
"""

import json
import os
import sys


def main():
    root = os.path.realpath(sys.argv[1])
    call = json.load(sys.stdin)
    args = call.get("tool_input") or {}
    path = args.get("file_path") or args.get("notebook_path")
    if not path:
        return
    target = os.path.realpath(os.path.join(call.get("cwd") or root, path))
    if target != root and not target.startswith(root + os.sep):
        print(f"{path} is outside {root}; this run may only write there", file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
