"""Compare a pre-switch baseline sample with a post-switch sample (task-57).

Inputs are JSONL files produced by upstream_baseline.py (aggregate lines are
ignored; only per-window lines are used, because the 15 minute sustain rule is
defined over windows, not over one lumped number).

Rule from the runbook: any of the four metrics worse than baseline by more than
--threshold-pct for --sustain consecutive windows means roll back.

Two request-count gates, because CPU/req is far noisier than the latency p95s:
  * --min-requests      (default 10)  latency and error metrics
  * --min-requests-cpu  (default 100) the CPU/request metric only

Usage (from api):
    ./.venv/Scripts/python.exe scripts/upstream_baseline_compare.py baseline.jsonl post.jsonl
    ./.venv/Scripts/python.exe scripts/upstream_baseline_compare.py base.jsonl post.jsonl \
        --threshold-pct 20 --sustain 3 --min-requests 10 --min-requests-cpu 100
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys

METRICS = (
    ("error_5xx_rate", "5xx_rate", True),
    ("p95_ttft_ms", "p95_ttft_ms", True),
    ("p95_total_ms", "p95_total_ms", True),
    ("cpu_ms_per_request", "cpu_ms_per_request", True),
)


def load_windows(path: str, min_requests: int) -> list[dict]:
    windows = []
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            if row.get("aggregate"):
                continue
            if (row.get("requests") or 0) < min_requests:
                continue
            windows.append(row)
    return windows


def med(windows: list[dict], key: str):
    vals = [float(w[key]) for w in windows if w.get(key) is not None]
    return round(statistics.median(vals), 4) if vals else None


def worse_series(post: list[dict], key: str, base: float, threshold: float) -> bool:
    tail = post[-3:] if len(post) >= 3 else post
    if not tail:
        return False
    for w in tail:
        v = w.get(key)
        if v is None or base is None or base == 0:
            return False
        if (float(v) - base) / base * 100.0 <= threshold:
            return False
    return True


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("baseline")
    ap.add_argument("post")
    ap.add_argument("--threshold-pct", type=float, default=20.0)
    ap.add_argument("--sustain", type=int, default=3,
                    help="consecutive post windows that must be worse (3 x 5 min = 15 min)")
    ap.add_argument("--min-requests", type=int, default=10,
                    help="drop windows with fewer requests than this (p95 noise guard)")
    ap.add_argument("--min-requests-cpu", type=int, default=100,
                    help="drop windows below this request count for the CPU metric only")
    args = ap.parse_args(argv)
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

    base = load_windows(args.baseline, args.min_requests)
    post = load_windows(args.post, args.min_requests)
    base_cpu = load_windows(args.baseline, args.min_requests_cpu)
    post_cpu = load_windows(args.post, args.min_requests_cpu)
    if not base or not post:
        print(json.dumps({"error": "not enough windows",
                          "baseline_windows": len(base), "post_windows": len(post)},
                         ensure_ascii=False))
        return 2

    out = {"baseline_windows": len(base), "post_windows": len(post),
           "threshold_pct": args.threshold_pct, "sustain_windows": args.sustain,
           "metrics": {}, "rollback": False}
    for key, label, lower_is_better in METRICS:
        use_cpu_gate = key == "cpu_ms_per_request"
        src_base = base_cpu if use_cpu_gate else base
        src_post = post_cpu if use_cpu_gate else post
        b = med(src_base, key)
        p = med(src_post, key)
        delta = (round((p - b) / b * 100.0, 2) if b and p is not None else None)
        sustained = worse_series(src_post, key, b, args.threshold_pct)
        out["metrics"][key] = {"label": label, "baseline_median": b, "post_median": p,
                               "delta_pct": delta, "worse_sustained": sustained,
                               "windows_used": [len(src_base), len(src_post)],
                               "min_requests_gate": (args.min_requests_cpu if use_cpu_gate
                                                     else args.min_requests),
                               "comparable": (bool(src_base) and bool(src_post)),
                               "insufficient_windows": not (bool(src_base) and bool(src_post)),
                               "note": ("CPU/请求只适合同进程同配置的相对对比；绝对值受请求大小/"
                                        "流式与否/上游 RTT 影响，不可跨场景引用"
                                        if use_cpu_gate else None)}
        if sustained:
            out["rollback"] = True
    if post and post[-1].get("pre_system_cpu_pct") is not None:
        out["post_pre_system_cpu_pct_last"] = post[-1]["pre_system_cpu_pct"]
    print(json.dumps(out, ensure_ascii=False))
    print("", file=sys.stderr)
    print("%-18s %12s %12s %9s %s" % ("metric", "baseline", "post", "delta%", "worse_sustained"),
          file=sys.stderr)
    for key, row in out["metrics"].items():
        print("%-18s %12s %12s %9s %s" % (row["label"], row["baseline_median"],
                                          row["post_median"], row["delta_pct"],
                                          row["worse_sustained"]), file=sys.stderr)
    for key, row in out["metrics"].items():
        if row.get("insufficient_windows"):
            print("WARNING: metric %s has no comparable windows at the %d-request gate; "
                  "its median is null and it cannot trigger a rollback"
                  % (key, row["min_requests_gate"]), file=sys.stderr)
    print("\nrollback=%s" % out["rollback"], file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
