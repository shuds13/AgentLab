# Output goes here

`run.sh` creates one directory per campaign under `workspace/`, and everything the
agent produces is written into it:

| | |
|---|---|
| `results.jsonl` | one line per measurement |
| `LOGBOOK.md` | terse memory across runs |
| `JOURNAL.md` | per-cycle write-up |
| `SKILL.md` | what the campaign established, for reuse elsewhere |
| `ANNOUNCEMENTS.md` | messages to a running agent |
| `runs/` | per-run metadata, heartbeat, prompt snapshots |
| `logs/` | agent logs |
| `config.json` | generated each launch from systems + users + campaign |

Nothing here is tracked by git.
