"""Run the api test suite across N shards (separate pytest processes).

Why: pytest is single-process here (no xdist), and the suite is dominated by
per-test app startup, SQLite schema creation and real-HTTP fixture teardown, so
wall time scales almost linearly with the number of processes. Each shard is an
independent pytest process with its own DB directory, its own pytest basetemp and
its own WAL directories, so shards cannot collide on files.

Usage:
    cd api
    .venv/Scripts/python.exe scripts/run_tests_parallel.py
    .venv/Scripts/python.exe scripts/run_tests_parallel.py --shards 6
    .venv/Scripts/python.exe scripts/run_tests_parallel.py --only test_probe.py,test_logs.py
    .venv/Scripts/python.exe scripts/run_tests_parallel.py -k "not probe" -- --durations=20

Exit code is 0 only when every shard exits 0. The summary JSON records per-shard
exit codes, wall times, pass/fail counts and the slowest tests of the run.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

API_DIR = Path(__file__).resolve().parents[1]          # api
REPO_ROOT = API_DIR.parents[1]                          # repo root
TESTS_DIR = API_DIR / "tests"
PYTHON = API_DIR / ".venv" / "Scripts" / "python.exe"
OUT_DIR = API_DIR / ".pytest-parallel"
LOCK_PATH = REPO_ROOT / ".scratch" / "bench2" / ".bench.lock"

# Per-file test time in seconds measured on 2026-11-14 from the JUnit report of a
# 4-shard run (660 tests, sum over shards 2753 s; the box was heavily loaded, so
# treat these as relative weights, not absolute idle-machine times). Used only to
# balance shards; missing files fall back to a line-count estimate.
# Refresh with:
#   scripts/run_tests_parallel.py --shards N
#   scripts/run_tests_parallel.py --emit-weights .pytest-parallel/summary.json --json w.json
# then paste the JSON keys/values here (keys are bare test file names).
DEFAULT_WEIGHTS: dict[str, float] = {
    "test_probe.py": 228.8,
    "test_channel_models.py": 220.9,
    "test_channels.py": 201.1,
    "test_breaker_api.py": 138.4,
    "test_keys_and_chat.py": 135.6,
    "test_model_types.py": 118.4,
    "test_logs.py": 109.1,
    "test_combos.py": 106.5,
    "test_channel_keys.py": 90.6,
    "test_auth.py": 83.8,
    "test_stability_invariants.py": 80.2,
    "test_implicit_groups.py": 75.7,
    "test_body_logging.py": 74.5,
    "test_streaming_chat.py": 70.2,
    "test_rounds.py": 70,
    "test_taxonomy_measure.py": 67.2,
    "test_schedule_api.py": 66.9,
    "test_zs2_catalog_cache.py": 65.1,
    "test_ledger_wal.py": 50.8,
    "test_rewrite.py": 49.8,
    "test_p0d_routing_shape.py": 46.1,
    "test_latency_metrics.py": 44.8,
    "test_settings.py": 41.7,
    "test_response_rewrite.py": 40.7,
    "test_dashboard.py": 37.9,
    "test_error_taxonomy.py": 34.4,
    "test_catalog.py": 34.3,
    "test_logs_summary.py": 33.1,
    "test_l1_db_merges.py": 31.3,
    "test_direct_native_model.py": 29.7,
    "test_schema.py": 29.5,
    "test_channel_discovery.py": 28.3,
    "test_log_split.py": 26.9,
    "test_ledger_read.py": 23.9,
    "test_timeseries.py": 21.9,
    "test_usage_sink.py": 20.9,
    "test_health.py": 19.4,
    "test_ledger_split_default.py": 14.4,
    "test_model_type_gate.py": 14,
    "test_retention.py": 12,
    "test_e2e_smoke.py": 10.6,
    "test_app_import.py": 10.4,
    "test_key_management.py": 9.8,
    "test_ttl_cache.py": 8.1,
    "test_channel_legacy_schema.py": 8.1,
    "test_postgres_dialect.py": 7.4,
    "test_usage_pipeline_wiring.py": 6.6,
    "test_redaction.py": 1,
    "test_httpx_transport.py": 0.6,
    "test_web_dist_mount.py": 0.4,
    "test_rewrite_actions.py": 0.1,
    "test_error_redaction.py": 0.1,
    "test_schedule.py": 0.1,
    "test_fake_upstream.py": 0.1,
    "test_breaker.py": 0,
    "test_failover_rules.py": 0,
    "test_repo_search.py": 0,
    "test_postgres_integration.py": 0,
}

DURATION_RE = re.compile(r"^(\d+(?:\.\d+)?)s\s+(call|setup|teardown)\s+(\S+)")


def log(message: str) -> None:
    stamp = datetime.now().strftime("%H:%M:%S")
    print(f"[{stamp}] {message}", flush=True)


# --------------------------------------------------------------------------- lock


def acquire_lock(task: str, wait_s: float) -> bool:
    """Grab the shared benchmark lock; returns False only when it stayed busy."""
    LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.time() + wait_s
    while True:
        try:
            fd = os.open(str(LOCK_PATH), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            owner = ""
            try:
                owner = LOCK_PATH.read_text(encoding="utf-8", errors="replace")[:300]
            except OSError:
                pass
            stale = False
            pid = 0
            try:
                meta = json.loads(owner)
                pid = int(meta.get("pid") or 0)
            except (ValueError, TypeError):
                match = re.search(r"pid=(\d+)", owner)
                if match:
                    pid = int(match.group(1))
            if pid:
                stale = not _pid_alive(pid)
            if stale:
                log(f"removing stale lock ({owner!r})")
                try:
                    LOCK_PATH.unlink()
                except OSError:
                    pass
                continue
            if time.time() >= deadline:
                log(f"lock busy, giving up: {owner!r}")
                return False
            log(f"lock busy, retrying in 5s: {owner!r}")
            time.sleep(5)
            continue
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump({"task": task, "pid": os.getpid(), "at": time.time()}, fh)
        return True


def release_lock() -> None:
    try:
        LOCK_PATH.unlink()
    except OSError:
        pass


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        import psutil  # type: ignore[import-not-found]

        return psutil.pid_exists(pid)
    except ImportError:
        return True


# ------------------------------------------------------------------ discovery


def discover(only: list[str] | None) -> list[Path]:
    files = sorted(TESTS_DIR.glob("test_*.py"))
    if not only:
        return files
    selected = []
    for path in files:
        if any(pattern in path.name for pattern in only):
            selected.append(path)
    missing = [pattern for pattern in only if not any(pattern in p.name for p in files)]
    if missing:
        raise SystemExit(f"--only matched no test file: {missing}")
    return selected


def estimate_seconds(path: Path) -> float:
    known = DEFAULT_WEIGHTS.get(path.name)
    if known is not None:
        return max(known, 0.5)
    # Fallback: a file costing seconds tracks its line count closely on this suite.
    return max(len(path.read_text(encoding="utf-8").splitlines()) / 40.0, 0.5)


def pack(files: list[Path], shards: int) -> list[list[Path]]:
    """Longest-processing-time first bin packing onto the requested bins."""
    weighted = sorted(files, key=estimate_seconds, reverse=True)
    bins: list[list[Path]] = [[] for _ in range(shards)]
    load = [0.0] * shards
    for path in weighted:
        target = min(range(shards), key=lambda index: (load[index], index))
        bins[target].append(path)
        load[target] += estimate_seconds(path)
    return [sorted(bin_, key=lambda p: p.name) for bin_ in bins]


# ------------------------------------------------------------------- running


def shard_command(files: list[Path], extra: list[str]) -> list[str]:
    # Paths are relative to API_DIR (the subprocess cwd), so keep the "tests/"
    # prefix: a bare file name does not resolve from the api/ directory.
    names = [f.relative_to(API_DIR).as_posix() for f in files]
    return [str(PYTHON), "-u", "-m", "pytest", "-q", "-p", "no:cacheprovider", *extra, *names]


def parse_counts(output: str) -> dict[str, object]:
    counts: dict[str, object] = {"passed": 0, "failed": 0, "errors": 0, "skipped": 0, "xfailed": 0}
    for line in output.splitlines():
        if (" passed" not in line and " failed" not in line and " error" not in line
                and " xfailed" not in line):
            continue
        matched = False
        for key, word in (("passed", "passed"), ("failed", "failed"), ("errors", "error"),
                          ("skipped", "skipped"), ("xfailed", "xfailed")):
            match = re.search(r"(\d+) " + word, line)
            if match:
                counts[key] = max(int(counts[key]), int(match.group(1)))  # type: ignore[call-overload]
                matched = True
        if matched:
            counts["summary"] = line.strip()
    return counts


def parse_slowest(output: str, limit: int) -> list[dict[str, object]]:
    rows = []
    for line in output.splitlines():
        match = DURATION_RE.match(line.strip())
        if match:
            rows.append({"seconds": float(match.group(1)), "phase": match.group(2), "test": match.group(3)})
    rows.sort(key=lambda row: float(row["seconds"]), reverse=True)
    return rows[:limit]


# ------------------------------------------------------------------- junit xml


def parse_junit(path: Path) -> dict[str, str]:
    """Map "tests/test_x.py::test_name" -> outcome from a pytest JUnit XML file."""
    cases: dict[str, str] = {}
    if not path.exists():
        return cases
    try:
        root = ET.parse(path).getroot()
    except (ET.ParseError, OSError):
        return cases
    for case in root.iter("testcase"):
        classname = case.get("classname", "")
        file = classname.replace(".", "/") + ".py" if classname else ""
        ident = f"{file}::{case.get('name', '')}"
        if case.find("failure") is not None:
            outcome = "failed"
        elif case.find("error") is not None:
            outcome = "error"
        elif case.find("skipped") is not None:
            outcome = "skipped"
        else:
            outcome = "passed"
        cases[ident] = outcome
    return cases


def junit_seconds_per_file(path: Path) -> dict[str, float]:
    """Per-file wall time summed from the testcase durations of a JUnit report."""
    totals: dict[str, float] = {}
    if not path.exists():
        return totals
    try:
        root = ET.parse(path).getroot()
    except (ET.ParseError, OSError):
        return totals
    for case in root.iter("testcase"):
        classname = case.get("classname", "")
        if not classname:
            continue
        file = classname.replace(".", "/") + ".py"
        try:
            seconds = float(case.get("time") or 0.0)
        except ValueError:
            seconds = 0.0
        totals[file] = totals.get(file, 0.0) + seconds
    return totals


def parse_collect_list(path: Path) -> set[str]:
    """Case ids from `pytest --collect-only -q` output (one id per line)."""
    ids: set[str] = set()
    if not path.exists():
        return ids
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.rstrip()
        if line.startswith("tests/") and "::" in line:
            ids.add(line)
    return ids


def compare_cases(expected: dict[str, str], actual: dict[str, str]) -> dict[str, object]:
    missing = sorted(set(expected) - set(actual))
    extra = sorted(set(actual) - set(expected))
    changed = sorted(ident for ident in set(expected) & set(actual) if expected[ident] != actual[ident])
    return {
        "expected": len(expected),
        "actual": len(actual),
        "missing": missing,
        "extra": extra,
        "changed": [(ident, expected[ident], actual[ident]) for ident in changed],
        "ok": not missing and not extra and not changed,
    }


def run_shard(index: int, files: list[Path], args: argparse.Namespace, extra: list[str]) -> dict[str, object]:
    out_dir = OUT_DIR / f"shard{index}"
    out_dir.mkdir(parents=True, exist_ok=True)
    db_dir = API_DIR / ".pytest-dbs" / f"shard{index}"
    db_dir.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    env["PYTHONPATH"] = "src"
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTEST_DB_DIR"] = str(db_dir)
    env["LEDGER_WAL_DIR"] = str(out_dir / "ledger")
    env["USAGE_WAL_DIR"] = str(out_dir / "usage")
    junit_path = out_dir / "junit.xml"
    cmd = shard_command(
        files,
        extra
        + [
            "--basetemp",
            str(out_dir / "tmp"),
            "--durations",
            str(args.durations),
            "--junitxml",
            str(junit_path),
        ],
    )
    log_path = out_dir / "pytest.log"
    log(f"shard {index}: {len(files)} files -> {", ".join(f.name for f in files)}")
    started = time.time()
    timed_out = False
    with open(log_path, "w", encoding="utf-8", errors="replace") as fh:
        proc = subprocess.Popen(cmd, cwd=str(API_DIR), env=env, stdout=fh, stderr=subprocess.STDOUT)
        try:
            rc = proc.wait(timeout=args.shard_timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            _kill_tree(proc)
            rc = proc.wait(timeout=60)
    duration = time.time() - started
    output = log_path.read_text(encoding="utf-8", errors="replace")
    counts = parse_counts(output)
    status = " (TIMEOUT)" if timed_out else ""
    log(f"shard {index} done rc={rc} in {duration:.1f}s{status}  {counts.get("summary", "")}")
    return {
        "shard": index,
        "files": [f.name for f in files],
        "rc": rc,
        "timed_out": timed_out,
        "duration_s": round(duration, 1),
        "counts": counts,
        "log": str(log_path),
        "junit": str(junit_path),
        "cases": len(parse_junit(junit_path)),
        "slowest": parse_slowest(output, 20),
    }


def _kill_tree(proc: subprocess.Popen) -> None:
    try:
        import psutil  # type: ignore[import-not-found]

        parent = psutil.Process(proc.pid)
        for child in parent.children(recursive=True):
            try:
                child.kill()
            except psutil.Error:
                pass
        parent.kill()
    except Exception:
        proc.kill()


# ---------------------------------------------------------------- weights dump


def emit_weights(summary_path: Path, output: Path) -> None:
    data = json.loads(summary_path.read_text(encoding="utf-8"))
    totals: dict[str, float] = {}
    for shard in data["shards"]:
        totals.update(
            {
                file: totals.get(file, 0.0) + seconds
                for file, seconds in junit_seconds_per_file(Path(str(shard.get("junit", "")))).items()
            }
        )
        if not totals:
            # Older summaries only kept the slowest rows; approximate from them.
            for row in shard.get("slowest", []):
                file = str(row["test"]).split("::", 1)[0]
                totals[file] = totals.get(file, 0.0) + float(row["seconds"])
    output.write_text(json.dumps(dict(sorted(totals.items())), indent=1), encoding="utf-8")
    log(f"wrote weights for {len(totals)} files -> {output}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--shards", type=int, default=max(1, min(4, (os.cpu_count() or 4) // 2)))
    parser.add_argument("--shard-timeout", type=float, default=1800.0, help="seconds before a shard is killed")
    parser.add_argument("--only", default="", help="comma-separated substrings of test file names")
    parser.add_argument("-k", dest="keyword", default="", help="pytest -k expression, forwarded to every shard")
    parser.add_argument("--durations", type=int, default=20, help="slowest tests reported per shard")
    parser.add_argument("--json", default=str(OUT_DIR / "summary.json"), help="summary output path")
    parser.add_argument("--compare-junit", default="", help="JUnit XML from a single-process run to compare case sets against")
    parser.add_argument("--compare-collect", default="", help="pytest --collect-only -q output to compare executed case ids against")
    parser.add_argument("--weights-file", default="", help="JSON {file: seconds} overriding DEFAULT_WEIGHTS")
    parser.add_argument("--emit-weights", default="", help="write per-file weights from a summary JSON and exit")
    parser.add_argument("--no-lock", action="store_true", help="do not take .bench.lock (quick subsets only)")
    parser.add_argument("--lock-wait", type=float, default=900.0, help="seconds to wait for a busy lock")
    parser.add_argument("--python", default="", help="override interpreter (default: api/.venv)")
    parser.add_argument("extra", nargs="*", help="extra pytest args (after --)")
    args = parser.parse_args(argv)

    if args.emit_weights:
        emit_weights(Path(args.emit_weights), Path(args.json))
        return 0

    global PYTHON
    if args.python:
        PYTHON = Path(args.python)
    if args.weights_file:
        loaded = json.loads(Path(args.weights_file).read_text(encoding="utf-8"))
        DEFAULT_WEIGHTS.update({str(k): float(v) for k, v in loaded.items()})

    only = [part.strip() for part in args.only.split(",") if part.strip()]
    files = discover(only)
    if not files:
        raise SystemExit("no test files selected")
    shards = max(1, min(args.shards, len(files)))
    bins = pack(files, shards)
    extra = []
    if args.keyword:
        extra += ["-k", args.keyword]
    extra += ["--rootdir", str(API_DIR)]
    extra += args.extra

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    locked = False
    if not args.no_lock:
        locked = acquire_lock("pytest-parallel", args.lock_wait)
        if not locked:
            log("continuing WITHOUT the benchmark lock")
    started_at = datetime.now(timezone.utc)
    t0 = time.time()
    results = []
    try:
        import concurrent.futures

        with concurrent.futures.ThreadPoolExecutor(max_workers=shards) as pool:
            futures = [pool.submit(run_shard, index, bin_, args, extra) for index, bin_ in enumerate(bins)]
            for future in concurrent.futures.as_completed(futures):
                results.append(future.result())
    finally:
        if locked:
            release_lock()
    wall = time.time() - t0
    results.sort(key=lambda item: int(item["shard"]))
    total = {"passed": 0, "failed": 0, "errors": 0, "skipped": 0}
    for row in results:
        counts = row["counts"]
        for key in total:
            total[key] += int(counts.get(key, 0))
    worst = sorted(
        [row for shard in results for row in shard["slowest"]],
        key=lambda item: float(item["seconds"]),
        reverse=True,
    )[:15]
    cases: dict[str, str] = {}
    for row in results:
        cases.update(parse_junit(Path(str(row.get("junit", "")))))
    comparison: dict[str, object] | None = None
    if args.compare_junit:
        expected = parse_junit(Path(args.compare_junit))
        comparison = compare_cases(expected, cases)
        log(
            "case comparison vs "
            + args.compare_junit
            + f": expected={comparison['expected']} actual={comparison['actual']}"
            + f" missing={len(comparison['missing'])} extra={len(comparison['extra'])}"
            + f" changed={len(comparison['changed'])}"
        )
        for ident in list(comparison["missing"])[:10]:
            log(f"  missing: {ident}")
        for ident in list(comparison["extra"])[:10]:
            log(f"  extra:   {ident}")
        for item in list(comparison["changed"])[:10]:
            log(f"  changed: {item[0]} {item[1]} -> {item[2]}")
    collect_compare: dict[str, object] | None = None
    if args.compare_collect:
        collected = parse_collect_list(Path(args.compare_collect))
        executed = set(cases)
        collect_compare = {
            "collected": len(collected),
            "executed": len(executed),
            "missing": sorted(collected - executed),
            "extra": sorted(executed - collected),
            "ok": collected == executed,
        }
        log(
            f"collect comparison vs {args.compare_collect}: collected={collect_compare['collected']}"
            f" executed={collect_compare['executed']} missing={len(collect_compare['missing'])}"
            f" extra={len(collect_compare['extra'])}"
        )
        for ident in list(collect_compare["missing"])[:10]:
            log(f"  missing: {ident}")
        for ident in list(collect_compare["extra"])[:10]:
            log(f"  extra:   {ident}")
    summary = {
        "started_at": started_at.isoformat(),
        "wall_s": round(wall, 1),
        "shards": results,
        "totals": total,
        "cases": cases,
        "comparison": comparison,
        "collect_comparison": collect_compare,
        "slowest": worst,
        "command": sys.argv,
    }
    json_path = Path(args.json)
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(summary, indent=1), encoding="utf-8")

    log(f"wall={wall:.1f}s  " + "  ".join(f"{k}={v}" for k, v in total.items()))
    log("slowest tests (call+setup+teardown):")
    for row in worst:
        log(f"  {row["seconds"]:7.2f}s {row["phase"]:<8} {row["test"]}")
    log(f"summary -> {json_path}")
    ok = all(int(row["rc"]) == 0 for row in results)
    if comparison is not None and not comparison["ok"]:
        ok = False
    if collect_compare is not None and not collect_compare["ok"]:
        ok = False
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
