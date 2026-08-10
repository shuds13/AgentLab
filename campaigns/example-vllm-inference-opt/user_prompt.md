Start the run.

Read `LOGBOOK.md` and `results.jsonl` and summarise where things stand. If they are
empty, this is the first run.

Reproduce the baseline first: `tp=8`, `dtype=bfloat16`, `max_num_seqs=128`,
`max_model_len=4096`, at `output_len` 128 and 2048 with the same input length. That
gives you a reference ms/token and your first `diagnostics` block. If the figure does
not land near 62 ms/token, understand that before going further.

Then open your first cycle.

If `SKILL.md` already exists in the shared area, read it — a previous run established
what is in it.
