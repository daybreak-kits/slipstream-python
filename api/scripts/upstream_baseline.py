"""Read-only baseline sampler for the upstream transport switch (task-57).

One sample = one observation window. Request accounting is aligned to
request_logs rowid growth inside that window (never to a wall-clock estimate),
so the numerator (rows) and the denominator (CPU seconds) cover the same span.

Read-only by construction:
  * sqlite is opened with uri mode=ro and PRAGMA query_only=1
  * only SELECT statements are issued
  * the sampler never creates, restarts or signals any process

Usage (production gateway, 30 min baseline in 5 min windows):
    python upstream_baseline.py --duration-s 1800 --interval-s 300

Usage (explicit pid + db, 2 min smoke):
    python upstream_baseline.py --pid 10464 --pid 22948 --db api/data/app.db \
        --duration-s 120

Output: one JSON line per window on stdout, plus a final aggregate line
(aggregate=true). Human-readable progress goes to stderr.
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import time
from pathlib import Path

import psutil

HERE = Path(__file__).resolve().parent      # .../api/scripts
API_DIR = HERE.parent                        # .../api
DEFAULT_DB = API_DIR / "data" / "app.db"
GATEWAY_MATCH = "l07_bootstrap.app_factory"


def log(msg: str) -> None:
    print("[baseline] " + msg, file=sys.stderr, flush=True)


def ro_connect(db_path: Path) -> sqlite3.Connection:
    uri = "file:" + str(db_path).replace("\\", "/") + "?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=5.0)
    conn.execute("PRAGMA query_only=1")
    return conn


def gateway_pids(port: int) -> list[int]:
    """Find the running gateway by command line; read-only, never signals."""
    found = []
    for p in psutil.process_iter(["pid", "name", "cmdline"]):
        try:
            cmd = p.info["cmdline"] or []
        except psutil.Error:
            continue
        joined = " ".join(cmd)
        if GATEWAY_MATCH not in joined:
            continue
        if ("--port" in cmd and str(port) in cmd[cmd.index("--port") + 1:cmd.index("--port") + 2]) or \
                ("--port=" + str(port)) in joined:
            found.append(p.info["pid"])
    return sorted(found)


def tree_pids(roots: list[int]) -> list[int]:
    """Union of the pid trees, deduplicated (a child must not be counted twice)."""
    seen = set()
    for pid in roots:
        try:
            proc = psutil.Process(pid)
            procs = [proc] + proc.children(recursive=True)
        except psutil.Error:
            continue
        for p in procs:
            seen.add(p.pid)
    return sorted(seen)


def tree_cpu_s(roots: list[int]) -> float:
    total = 0.0
    for pid in tree_pids(roots):
        try:
            ct = psutil.Process(pid).cpu_times()
        except psutil.Error:
            continue
        total += ct.user + ct.system
    return total


def p95(values: list[float]):
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return round(float(ordered[0]), 1)
    rank = 0.95 * (len(ordered) - 1)
    low, high = int(rank), min(int(rank) + 1, len(ordered) - 1)
    frac = rank - low
    return round(float(ordered[low] + (ordered[high] - ordered[low]) * frac), 1)


def row_watermark(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT COALESCE(MAX(rowid), 0) FROM request_logs").fetchone()
    return int(row[0] or 0)


def fetch_rows(conn: sqlite3.Connection, watermark: int) -> list[tuple]:
    return conn.execute(
        "SELECT http_status, ttft_ms, total_ms FROM request_logs WHERE rowid > ?",
        (watermark,)).fetchall()


def summarise(rows: list[tuple], cpu_s: float, window_s: float) -> dict:
    requests = len(rows)
    five_xx = sum(1 for r in rows if int(r[0] or 0) >= 500)
    ttft = [float(r[1]) for r in rows if r[1] is not None]
    total = [float(r[2]) for r in rows if r[2] is not None]
    return {
        "window_s": round(window_s, 1),
        "requests": requests,
        "cpu_s": round(cpu_s, 3),
        "cpu_ms_per_request": (round(cpu_s * 1000.0 / requests, 3) if requests else None),
        "p95_ttft_ms": p95(ttft),
        "p95_total_ms": p95(total),
        "error_5xx_rate": (round(five_xx / requests, 6) if requests else None),
        "samples": {"ttft": len(ttft), "total": len(total), "five_xx": five_xx},
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pid", type=int, action="append", default=[],
                    help="gateway pid (repeatable); defaults to $GW_PIDS or auto-detect")
    ap.add_argument("--port", type=int, default=int(os.environ.get("GW_PORT", "3680")),
                    help="port used for auto-detection (default 3680)")
    ap.add_argument("--db", default=os.environ.get("BASELINE_DB", str(DEFAULT_DB)))
    ap.add_argument("--duration-s", type=float, default=120.0)
    ap.add_argument("--interval-s", type=float, default=0.0,
                    help="sub-window length; 0 means one window covering duration")
    ap.add_argument("--quiet", action="store_true", help="no stderr progress lines")
    args = ap.parse_args(argv)

    db_path = Path(args.db)
    if not db_path.is_absolute():
        db_path = (Path.cwd() / db_path).resolve()
    if not db_path.exists():
        # cwd-agnostic fallbacks: run from api, from the repo root, or anywhere
        for cand in (API_DIR / "data" / "app.db",
                     Path.cwd() / "api" / "data" / "app.db"):
            if cand.exists():
                db_path = cand
                break
    if not db_path.exists():
        log("database not found: %s" % db_path)
        return 2

    pids = list(args.pid)
    if not pids and os.environ.get("GW_PIDS"):
        pids = [int(x) for x in os.environ["GW_PIDS"].split(",") if x.strip()]
    if not pids:
        pids = gateway_pids(args.port)
    if not pids:
        log("no gateway pid found (port %d); pass --pid or GW_PIDS" % args.port)
        return 3
    if not args.quiet:
        log("pids=%s tree=%d procs db=%s alignment=request_logs_rowid_delta"
            % (pids, len(tree_pids(pids)), db_path))

    interval = args.interval_s if args.interval_s and args.interval_s > 0 else args.duration_s
    n_windows = max(1, int(round(args.duration_s / interval)))
    conn = ro_connect(db_path)
    all_rows: list[tuple] = []
    all_cpu = 0.0
    t_all0 = time.time()

    for idx in range(n_windows):
        pre_cpu = psutil.cpu_percent(interval=1.0)
        watermark = row_watermark(conn)
        cpu0 = tree_cpu_s(pids)
        t0 = time.time()
        deadline = t0 + interval
        while time.time() < deadline:
            time.sleep(min(5.0, max(0.2, deadline - time.time())))
        rows = fetch_rows(conn, watermark)
        cpu_s = max(0.0, tree_cpu_s(pids) - cpu0)
        window_s = time.time() - t0
        out = summarise(rows, cpu_s, window_s)
        out.update({
            "pre_system_cpu_pct": round(pre_cpu, 1),
            "window_alignment": "request_logs_rowid_delta",
            "window_index": idx + 1,
            "window_start": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(t0)),
            "pids": pids,
            "db": str(db_path),
            "aggregate": False,
        })
        print(json.dumps(out, ensure_ascii=False), flush=True)
        all_rows.extend(rows)
        all_cpu += cpu_s

    if n_windows > 1:
        agg = summarise(all_rows, all_cpu, time.time() - t_all0)
        agg.update({
            "pre_system_cpu_pct": None,
            "window_alignment": "request_logs_rowid_delta",
            "windows": n_windows,
            "pids": pids,
            "db": str(db_path),
            "aggregate": True,
        })
        print(json.dumps(agg, ensure_ascii=False), flush=True)

    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
