"""Runs one vLLM benchmark configuration on a remote compute node.

Defines the four names the framework imports: JOB_DESC, JOB_SCHEMA, job_key and
remote_fn.

A job builds a `vllm bench` command from the arguments, runs it in the environment
described by the target block of config.json, writes the full output to a log on the
compute filesystem, and returns the parsed metrics together with startup diagnostics
(platform, attention backend, graph capture, KV cache size, version, warnings).

Environment variables in `env_extra` are applied before the benchmark starts, so
runtime settings can be varied without editing this file.
"""

JOB_DESC = """
Run one vLLM benchmark configuration on a compute node.

Arguments:

  bench_mode            'throughput' reports tokens/s over num_prompts. 'latency'
                        reports end-to-end seconds for one batch. Per-token decode rate
                        is the latency difference between two output_len values at the
                        same input_len.
  input_len             prompt length in tokens.
  output_len            generation length in tokens.
  tensor_parallel_size  devices the model is split across.
  dtype                 'auto', 'bfloat16' or 'float16'.
  enforce_eager         true disables graph capture and the compilation path.
  max_num_seqs          scheduler concurrency.
  max_model_len         context length, which sets KV cache sizing.
  num_prompts           number of prompts, in throughput mode.
  env_extra             environment variables applied before vLLM starts, for runtime
                        settings that are not command-line flags.
  note                  why you chose this configuration.

Returns throughput_tokens_per_sec and avg_latency_sec as applicable, a diagnostics
block parsed from the vLLM startup output (platform, attention backend, graph capture,
KV cache blocks, version, warnings), and the path to the full log on the compute
filesystem.
"""

JOB_SCHEMA = {
    "bench_mode": str,
    "input_len": int,
    "output_len": int,
    "tensor_parallel_size": int,
    "dtype": str,
    "enforce_eager": bool,
    "max_num_seqs": int,
    "max_model_len": int,
    "num_prompts": int,
    "env_extra": dict,
    "note": str,
}


def _norm(args):
    """Fill defaults. Kept module-level so job_key and remote_fn agree."""
    return {
        "bench_mode": str(args.get("bench_mode", "throughput")),
        "input_len": int(args.get("input_len", 128)),
        "output_len": int(args.get("output_len", 128)),
        "tensor_parallel_size": int(args.get("tensor_parallel_size", 8)),
        "dtype": str(args.get("dtype", "bfloat16")),
        "enforce_eager": bool(args.get("enforce_eager", False)),
        "max_num_seqs": int(args.get("max_num_seqs", 128)),
        "max_model_len": int(args.get("max_model_len", 4096)),
        "num_prompts": int(args.get("num_prompts", 100)),
        "env_extra": dict(args.get("env_extra", {}) or {}),
    }


def job_key(args):
    """Stable identity, so the same configuration is not run twice."""
    a = _norm(args)
    env = ",".join(f"{k}={v}" for k, v in sorted(a["env_extra"].items()))
    return (
        f"{a['bench_mode']}/in{a['input_len']}_out{a['output_len']}"
        f"/tp{a['tensor_parallel_size']}/{a['dtype']}"
        f"/eager{int(a['enforce_eager'])}/seqs{a['max_num_seqs']}"
        f"/mml{a['max_model_len']}/np{a['num_prompts']}/env[{env}]"
    )


def remote_fn(args, target):
    """Runs on an Aurora compute node, launched by Globus Compute.

    Shipped BY SOURCE: it cannot see this module's imports or globals. Everything is
    imported inside the body or arrives via args / target. That is a requirement, not
    a style choice.
    """
    import json
    import os
    import re
    import subprocess
    import time

    # --- normalise args (cannot call _norm; not visible on the worker) ---
    bench_mode = str(args.get("bench_mode", "throughput"))
    input_len = int(args.get("input_len", 128))
    output_len = int(args.get("output_len", 128))
    tp = int(args.get("tensor_parallel_size", 8))
    dtype = str(args.get("dtype", "bfloat16"))
    enforce_eager = bool(args.get("enforce_eager", False))
    max_num_seqs = int(args.get("max_num_seqs", 128))
    max_model_len = int(args.get("max_model_len", 4096))
    num_prompts = int(args.get("num_prompts", 100))
    env_extra = dict(args.get("env_extra", {}) or {})

    model = target.get("model", "meta-llama/Meta-Llama-3-70B")
    work_dir = target.get("work_dir", ".")
    timeout = int(target.get("timeout", 5400))

    os.makedirs(work_dir, exist_ok=True)
    tag = (f"{bench_mode}_in{input_len}_out{output_len}_tp{tp}_{dtype}"
           f"_eager{int(enforce_eager)}_seqs{max_num_seqs}_{int(time.time())}")
    log_path = os.path.join(work_dir, f"{tag}.log")

    # --- environment ---
    env = dict(os.environ)
    for k, v in (target.get("env") or {}).items():
        env[str(k)] = str(v)
    for k, v in env_extra.items():          # agent's overrides win
        env[str(k)] = str(v)

    # --- build the vLLM command ---
    cmd = ["vllm", "bench", bench_mode,
           "--model", model,
           "--input-len", str(input_len),
           "--output-len", str(output_len),
           "--dtype", dtype,
           "--tensor-parallel-size", str(tp),
           "--max-model-len", str(max_model_len),
           "--max-num-seqs", str(max_num_seqs)]
    if bench_mode == "latency":
        cmd += ["--batch-size", "1", "--num-iters-warmup", "2", "--num-iters", "2"]
    else:
        cmd += ["--num-prompts", str(num_prompts)]
    if enforce_eager:
        cmd += ["--enforce-eager"]

    # The module load has to happen in the same shell as vllm, so go through bash.
    setup = target.get("worker_setup", "")
    shell_cmd = (setup + " && " if setup else "") + " ".join(
        "'" + c + "'" if " " in c else c for c in cmd)

    started = time.time()
    try:
        proc = subprocess.run(["bash", "-lc", shell_cmd], capture_output=True,
                              text=True, timeout=timeout, env=env)
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}", "cmd": shell_cmd, "key_env": env_extra}

    out = (proc.stdout or "") + "\n" + (proc.stderr or "")

    # Always keep the full log. The existing benchmark discards these, and the vLLM
    # startup banner is the most informative artefact available.
    try:
        with open(log_path, "w") as f:
            f.write("$ " + shell_cmd + "\n\nENV_EXTRA: " + json.dumps(env_extra) + "\n\n" + out)
    except Exception:
        pass

    # --- startup diagnostics: what did vLLM actually do? ---
    diagnostics = {}
    patterns = {
        "platform":         r"[Pp]latform[:\s]+(\S+)",
        "attention_backend": r"[Uu]sing (\S+) backend",
        "graph_capture":    r"(?i)(graph capturing finished|CUDA graphs|Capturing.*graph|enforce_eager)",
        "kv_cache_blocks":  r"(?i)GPU KV cache size[:\s]+([\d,]+)",
        "num_devices":      r"(?i)(?:world_size|tensor.parallel.size)[=:\s]+(\d+)",
        "vllm_version":     r"(?i)vLLM (?:API server )?version[:\s]+(\S+)",
    }
    for name, pat in patterns.items():
        m = re.search(pat, out)
        if m:
            diagnostics[name] = m.group(1) if m.groups() else m.group(0)
    warn = [l.strip() for l in out.splitlines()
            if re.search(r"(?i)\b(warning|fallback|not supported|disabled)\b", l)]
    if warn:
        diagnostics["warnings"] = warn[:15]

    if proc.returncode != 0:
        return {"error": out[-3000:], "cmd": shell_cmd, "log": log_path,
                "diagnostics": diagnostics, "key_env": env_extra}

    # --- metrics ---
    result = {"bench_mode": bench_mode, "model": model, "input_len": input_len,
              "output_len": output_len, "tensor_parallel_size": tp, "dtype": dtype,
              "enforce_eager": enforce_eager, "max_num_seqs": max_num_seqs,
              "max_model_len": max_model_len, "env_extra": env_extra,
              "wall_seconds": round(time.time() - started, 1),
              "log": log_path, "diagnostics": diagnostics}

    m = re.search(r"Throughput:\s*([\d.]+)\s*requests/s,\s*([\d.]+)\s*(?:total\s+)?tokens/s", out)
    if m:
        result["requests_per_sec"] = float(m.group(1))
        result["throughput_tokens_per_sec"] = float(m.group(2))
    m = re.search(r"Avg latency:\s*([\d.]+)", out)
    if m:
        result["avg_latency_sec"] = float(m.group(1))

    if "throughput_tokens_per_sec" not in result and "avg_latency_sec" not in result:
        result["error"] = "ran cleanly but no metric parsed -- check the log"
        result["stdout_tail"] = out[-1500:]
    return result
