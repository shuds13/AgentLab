#!/usr/bin/env python3
"""
Watch a running campaign in a browser: the agent's log as it is written, and the files
it is writing.

A run is long and quiet -- minutes can pass inside one turn with nothing printed -- and
whoever started it usually has no terminal attached to it. This serves the same files
they would otherwise `tail`, on localhost, so a run can be followed without touching it.

Usage:
    python watch.py [campaign] [--port 8765] [--no-open]

It reads the campaign's workspace and serves what it finds. The campaign named on the
command line is the one it opens on, and with none named it opens on the one most
recently active; the page can switch to any other campaign in the lab, since one
watcher can serve them all. What it writes: a message you type, which goes to the board
the agent reads between turns; and a change to a run's budgets, on the budgets tab, for
runs of the user it runs as. Both need the page opened with the link it prints, which
carries its key. Stop it with Ctrl-C; the run is unaffected either way.

The server is the watcher/ package beside this file, and the page is its static/.
"""

from watcher.server import main

if __name__ == "__main__":
    main()
