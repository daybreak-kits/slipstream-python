# Testing the API (`api`)

All API tests live in `api/tests` and run against the real application
factory (`l07_bootstrap.app_factory.create_app`) with a throwaway SQLite
database per test. No network access and no external service is required.

## Running the suite

```bash
cd api
PYTHONPATH=src .venv/Scripts/python.exe -m pytest -q -p no:cacheprovider

# Linux (production machine)
PYTHONPATH=src ../.venv/bin/python -m pytest -q -p no:cacheprovider
```

`-p no:cacheprovider` matters on this repo: the sandbox refuses to create
`__pycache__`/cache directories in some locations and pytest otherwise warns or
fails on cache writes.

Single file / single test:

```bash
cd api
PYTHONPATH=src .venv/Scripts/python.exe -m pytest -q -p no:cacheprovider tests/test_auth.py
PYTHONPATH=src .venv/Scripts/python.exe -m pytest -q -p no:cacheprovider tests/test_auth.py::test_password_change_invalidates_old_password
```

## Running the suite in parallel

### Why

Every test builds a fresh application and runs its full lifespan (gateway
clients, schema creation, default-admin seeding), so a fixed startup cost is
paid per test rather than per run. On this box that makes the suite almost
entirely CPU- and I/O-serial, and a single pytest process leaves most cores
idle. No `pytest-xdist`/`pytest-timeout` is available here and packages cannot
be installed, so `api/scripts/run_tests_parallel.py` shards the suite
itself.

### How it works

The script:

1. discovers `tests/test_*.py` (or a subset selected with `--only`);
2. estimates each file's cost from `DEFAULT_WEIGHTS` (measured per-file times,
   refreshed with `--emit-weights`) and LPT-packs files onto `--shards` bins —
   whole files only, so collection order inside a file is unchanged;
3. starts one independent pytest subprocess per bin, all in parallel;
4. aggregates exit codes, per-shard counts, the slowest tests, a JUnit report
   per shard and — optionally — a case-by-case comparison against a reference.

```bash
cd api
PYTHONPATH=src .venv/Scripts/python.exe scripts/run_tests_parallel.py            # 4 shards by default
PYTHONPATH=src .venv/Scripts/python.exe scripts/run_tests_parallel.py --shards 6
PYTHONPATH=src .venv/Scripts/python.exe scripts/run_tests_parallel.py --only probe,auth -k "not stream"
PYTHONPATH=src .venv/Scripts/python.exe scripts/run_tests_parallel.py -- --maxfail=1
```

Useful flags: `--shards N`, `--shard-timeout S` (kills a stuck shard),
`--only a,b` (file-name substrings), `-k EXPR` (forwarded to every shard),
`--json PATH`, `--weights-file PATH`, `--compare-junit PATH`,
`--no-lock`, `--lock-wait S`, `--python PATH`; anything after `--` is passed
through to pytest.

### Isolation

Each shard has its own `--basetemp`, its own `PYTEST_DB_DIR`, and its own
`LEDGER_WAL_DIR`/`USAGE_WAL_DIR`, so shards never share SQLite files or WAL
directories. Tests must not bind fixed ports (use the fake-upstream fixtures),
which keeps shards independent; see "Writing new tests".

### Lock

A full suite is a heavy-load job on a machine that also runs the production
gateway, so the script takes the shared benchmark lock
(`.scratch/bench2/.bench.lock`) unless `--no-lock` is passed. A lock
left behind by a dead pid is reclaimed automatically; while another owner is
alive the script waits up to `--lock-wait` seconds, then continues anyway and
says so. Use `--no-lock` only for quick subsets.

### Outputs and exit code

Everything lands in `api/.pytest-parallel/`:

- `shardN/pytest.log` — raw pytest output for that bin;
- `shardN/junit.xml` — JUnit report used for case comparison and weights;
- `summary.json` — wall clock, per-shard exit code/counts/slowest tests, the
  executed case map, and the result of `--compare-junit` when requested.

The exit code is non-zero if any shard fails/times out, or if a requested case
comparison finds a missing, extra or changed case — so it is safe to use as a
gate.

### Refreshing the weights

Weights only affect balance, not correctness. After a serious suite change:

```bash
cd api
PYTHONPATH=src .venv/Scripts/python.exe scripts/run_tests_parallel.py --shards 4
PYTHONPATH=src .venv/Scripts/python.exe scripts/run_tests_parallel.py \
    --emit-weights .pytest-parallel/summary.json --json weights.json
```

Then paste `weights.json` (bare file names) into `DEFAULT_WEIGHTS` in the
script. Treat the numbers as relative costs; they were measured on a loaded box
and are only used for packing.

### Proving the shards covered everything

Compare the executed case ids against pytest's own collector:

```bash
cd api
PYTHONPATH=src .venv/Scripts/python.exe -m pytest --collect-only -q -p no:cacheprovider \
    > collect.txt
PYTHONPATH=src .venv/Scripts/python.exe scripts/run_tests_parallel.py \
    --compare-collect collect.txt
```

`--compare-collect` adds the collected ids to `summary.json`
(`collect_comparison`) and fails the run when a collected test is missing from
the shards or a shard ran something the collector did not see. `--compare-junit
ref.xml` does the same against a reference single-process run, including each
case's outcome.

Both comparisons assume the working tree is stable while the suite runs: if
someone edits test files mid-run the two sets drift for reasons unrelated to
sharding, so run the comparison on a quiet tree.

## Fixtures (tests/conftest.py)

- `database_url` — a fresh SQLite file inside `api/.pytest-dbs`, removed
  after the test. Override the directory with `PYTEST_DB_DIR` (the parallel
  runner does this per shard).
- `test_settings` — `Settings` pointed at that database, fixed JWT secret and
  `admin/admin` credentials.
- `app` / `client` — the application and an in-process ASGI client; the
  `lifespan_context` runs, so migrations and the default-admin seeding happen
  exactly like in production.
- `fake_upstream` / `fake_upstream_client` — an in-process ASGI OpenAI-compatible
  upstream for gateway tests. Tests that need real HTTP (streaming, protocol
  detection, probes) start `FakeUpstreamServer` from
  `tests/fake_upstream_server.py` on an ephemeral localhost port instead.

Two fixture-level speedups are in place and should be preserved:

1. `FakeUpstreamServer` polls its socket every couple of milliseconds instead of
   the `socketserver` default half second, and uses daemon request threads, so
   teardown no longer waits for a poll tick.
2. The default-admin PBKDF2 digest is cached per password for the whole session.
   Hashing itself is unchanged — the cached value comes from the production
   `hash_password` and `verify_password` still verifies the stored digest — the
   cache only avoids redoing the same expensive derivation for every fresh
   database.

Both are test-infrastructure choices, not product behaviour; `src/` is not
involved.

## Writing new tests

- Prefer the `client` fixture and drive the public HTTP API; tests that reach
  into repositories are fine for schema/migration coverage.
- Keep tests independent: never rely on state left behind by another test, and
  do not assume a specific database file name.
- Do not bind fixed ports. Use `FakeUpstreamServer` (ephemeral port) or the
  ASGI `fake_upstream` fixture.
- Avoid real `time.sleep` where a shorter poll or an injected delay works;
  long waits turn into suite-wide latency. If a genuine timing assertion needs
  a sleep, comment the bound.
- Mark known-flaky tests explicitly instead of loosening unrelated assertions.

## Known flakes

- `tests/test_zs2_catalog_cache.py::test_key_revoke_visible_within_poll_interval`
  — depends on the catalog poll interval and occasionally needs more than one
  poll cycle under load. It is expected to pass on a rerun.

## Measuring a before/after change

Set `PYTEST_NO_FAST_FIXTURES=1` to restore the old fixture behaviour (half
second `serve_forever` poll, uncached PBKDF2). Run the same file twice in the
same time window — once with the variable, once without — and compare the sum
of per-case times from `--junitxml`, not the total wall clock: process start-up
(`import litellm` and friends) dominates short runs and is not affected by the
fixtures.

## Before trusting a timing number: sample the machine state

This box also runs the production gateway and whatever benchmarks the team has
started, and its throughput swings by an order of magnitude between quiet and
loaded periods. Take a three-line CPU baseline first and record it next to any
suite timing:

```bash
cd api
PYTHONPATH=src .venv/Scripts/python.exe - <<'PY'
import time
def bench(label, fn, n=3):
    xs = []
    for _ in range(n):
        t = time.perf_counter(); fn(); xs.append((time.perf_counter() - t) * 1000)
    print(label, [round(x, 1) for x in xs], "ms")
def loop():
    total = 0
    for i in range(6_000_000):
        total += i
bench("empty_loop_6M", loop)
from l02_infra.crypto import hash_password
bench("pbkdf2_240k", lambda: hash_password("probe"))
t = time.perf_counter(); import litellm  # noqa: F401
print("import_litellm", round((time.perf_counter() - t) * 1000), "ms")
PY
```

If the loop or the PBKDF2 numbers are several times higher than the values you
saw on an idle machine, the box is saturated (and the wall clock of anything you
measure is meaningless): compare relative numbers from the same window, or wait
for a quiet window. Also check that no other pytest/benchmark process is
running and that `.scratch/bench2/.bench.lock` is free before blaming
the code for a regression.

Load inflates *every* part of the suite, including the per-test application
lifespan, so a slowed suite on a busy box is not evidence that the suite itself
regressed.

## Frontend (`web`)

The web app has its own gates, run from `web`; they are independent of the
API suite above.

| Command | What it proves |
|---|---|
| `npm run build` | `tsc --noEmit` passes and the production bundle builds (`vite build`; chunk-size guard in `vite.config.ts`) |
| `npm test -- --run` | vitest component/flow tests (bare `npm test` is watch mode) |
| `npm run test:visual` | Playwright screenshot suite against a dev server it starts itself on **port 3690** |
| `npm run test:visual:update` | Re-records the 7 baseline screenshots after an intended visual change |
| `npm run lint:ui` | UI discipline gate: `node scripts/lint-ui.mjs --max-warnings 19` |

Things that cost time if forgotten:

- **Port 3690 belongs to the visual suite; 3680 is the gateway.** The Playwright
  config starts its own server with `reuseExistingServer: false` on purpose:
  pointing it at 3680 silently tests the gateway's stale `dist` build, and the
  symptom is "the source changed but the test still fails".
- Visual diff threshold is `maxDiffPixelRatio: 0.002` (`playwright.config.ts`).
- `src/test/setup.ts` stubs four browser APIs jsdom lacks — `ResizeObserver`,
  the `hasPointerCapture`/`setPointerCapture`/`releasePointerCapture` trio,
  `scrollIntoView`, and a never-matching `matchMedia`. Any test that renders a
  Radix Select/Popover/Tooltip throws on the first screen without them.
- `lint:ui` splits its counts: application-code violations fail the gate, while
  vendored shadcn sources under `src/components/ui/**` are reported in a
  separate "relaxed" bucket. The current ceiling (19 warnings) is exactly that
  vendor exception count — the per-batch ladder is in
  `docs/ui-adoption-map.md` §9, and the run's real-environment notes in §10.
- Numbers (test counts, timings, bundle sizes) live in
  `docs/ui-adoption-map.md` §8/§10 and `PERFORMANCE_REPORT.md`, not here —
  counting discipline, `ssot.md` §16.

