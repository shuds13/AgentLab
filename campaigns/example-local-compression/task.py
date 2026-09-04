"""One job: compress a fixed corpus with one zlib configuration and time the result.

Only local_fn is defined, so this campaign needs no Globus Compute endpoint and no
users/<you>/local.json. The framework offers the local job tools and nothing else.
"""

# Cost model. A job is scored on the time to get the corpus to a consumer: the time
# spent compressing plus the time spent sending what compression produced. Turning the
# ratio into seconds is what makes the two halves comparable, and it is why the best
# level is not the highest one -- past a point the encoder costs more time than the
# smaller payload saves.
BANDWIDTH_MB_S = 5.0
CORPUS_MB = 8

LOCAL_DESC = (
    """
Compress the benchmark corpus with one zlib configuration and return its timings.

Returns `total_seconds` -- encode time plus transmit time at
%.1f MB/s -- which is the number to minimise, alongside the two halves separately
(`encode_seconds`, `transmit_seconds`), the compression `ratio`, and `encode_mb_s`.

Parameters:
  level     1-9. Higher searches harder for matches: smaller output, slower encode.
  strategy  default | filtered | huffman_only | rle | fixed. How the encoder looks for
            matches. huffman_only and rle skip match-finding almost entirely.
  memlevel  1-9. Working memory for the encoder. Higher can be faster and compress
            better at the cost of memory.
  windowlog 9-15. log2 of the match window, so 15 is a 32 KiB window. A larger window
            finds more distant repeats.

Each run rebuilds the same corpus from a fixed seed, so results are comparable across
jobs. A job takes a few seconds.
"""
    % BANDWIDTH_MB_S
)

LOCAL_SCHEMA = {"level": int, "strategy": str, "memlevel": int, "windowlog": int}

_STRATEGIES = {
    "default": "Z_DEFAULT_STRATEGY",
    "filtered": "Z_FILTERED",
    "huffman_only": "Z_HUFFMAN_ONLY",
    "rle": "Z_RLE",
    "fixed": "Z_FIXED",
}


def _corpus(mb):
    """Deterministic mixed-entropy corpus: mostly repeated vocabulary, some noise, so
    that both match-finding and entropy coding have something to do."""
    import random

    rnd = random.Random(1234)
    words = [
        "alpha",
        "beta",
        "gamma",
        "delta",
        "epsilon",
        "zeta",
        "eta",
        "theta",
        "run",
        "job",
        "node",
        "rank",
        "queue",
        "kernel",
        "buffer",
        "stride",
    ]
    out, size, target = [], 0, mb * 1024 * 1024
    while size < target:
        if rnd.random() < 0.75:
            line = " ".join(rnd.choice(words) for _ in range(12)) + "\n"
        else:
            line = "".join(chr(rnd.randint(33, 126)) for _ in range(60)) + "\n"
        out.append(line)
        size += len(line)
    return "".join(out).encode()


def local_fn(args):
    import time
    import zlib

    level = int(args.get("level", 6))
    strategy = str(args.get("strategy", "default"))
    memlevel = int(args.get("memlevel", 8))
    windowlog = int(args.get("windowlog", 15))

    if not 1 <= level <= 9:
        return {"error": f"level must be 1-9, got {level}", "args": args}
    if strategy not in _STRATEGIES:
        return {
            "error": f"strategy must be one of {sorted(_STRATEGIES)}, got {strategy!r}",
            "args": args,
        }
    if not 1 <= memlevel <= 9:
        return {"error": f"memlevel must be 1-9, got {memlevel}", "args": args}
    if not 9 <= windowlog <= 15:
        return {"error": f"windowlog must be 9-15, got {windowlog}", "args": args}

    data = _corpus(CORPUS_MB)
    zstrategy = getattr(zlib, _STRATEGIES[strategy])

    start = time.perf_counter()
    compressor = zlib.compressobj(level, zlib.DEFLATED, windowlog, memlevel, zstrategy)
    blob = compressor.compress(data) + compressor.flush()
    encode_seconds = time.perf_counter() - start

    transmit_seconds = len(blob) / (BANDWIDTH_MB_S * 1024 * 1024)
    return {
        "args": {
            "level": level,
            "strategy": strategy,
            "memlevel": memlevel,
            "windowlog": windowlog,
        },
        "total_seconds": round(encode_seconds + transmit_seconds, 4),
        "encode_seconds": round(encode_seconds, 4),
        "transmit_seconds": round(transmit_seconds, 4),
        "ratio": round(len(data) / len(blob), 3),
        "encode_mb_s": round(len(data) / (1024 * 1024) / encode_seconds, 2),
        "raw_bytes": len(data),
        "compressed_bytes": len(blob),
        "diagnostics": {
            "bandwidth_mb_s": BANDWIDTH_MB_S,
            "corpus_mb": CORPUS_MB,
            "zlib_version": zlib.ZLIB_VERSION,
        },
    }
