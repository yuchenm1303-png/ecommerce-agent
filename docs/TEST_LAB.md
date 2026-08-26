# Ecommerce-Agent Test Lab

`tools/test_lab.py` is the default developer regression entrypoint when code should be tested without spending AI quota or requiring a real supplier listing run.

## Start here

On Windows you can double-click:

```text
run_test_lab.bat
```

or run directly:

```bash
python tools/test_lab.py --all
```

`--all` is deliberately comprehensive: it runs the complete repository `tests/` tree with `-m "not probe"`. New ordinary pytest tests are therefore included automatically; only tests explicitly marked `probe` are excluded as live-browser checks.

For a fast development loop, run one area only:

```bash
python tools/test_lab.py --suite vertical
python tools/test_lab.py --suite cdp --stress-rounds 25
python tools/test_lab.py --suite resolver --suite fill_plan
```

List targeted suites:

```bash
python tools/test_lab.py --list
```

Write a machine-readable report:

```bash
python tools/test_lab.py --all --json-report logs/test-lab/latest.json
```

## Safety contract

A Test Lab run is intentionally different from a production smoke test:

- paid AI credential environment variables are removed from every pytest child process;
- external socket connections are blocked by `app.test_lab_pytest_plugin`;
- loopback remains available for local fixture servers, but production CDP ports `9222` and `9333` are explicitly blocked inside pytest;
- HTTP(S) proxy variables inherited by nested child processes point to a closed loopback port;
- each suite owns a fresh writable temp root under `logs/test-lab/tmp/`; `TEMP`, `TMP`, `TMPDIR`, and pytest `--basetemp` are all pinned there so host/system temp permissions cannot affect the run;
- all tests explicitly marked `probe` are excluded from `--all` and targeted suites;
- Test Lab itself never launches the real Amazon/Makro workflow and never clicks Save or Send to QC.

If a test unexpectedly tries to escape to the network it fails with `TEST_LAB_NETWORK_BLOCKED` instead of silently spending quota.

## Targeted suite map

- `source`: Supplier URL identity and source-cache identity.
- `vertical`: full query-ladder sampling, global candidate ownership, current-generation binding, prior-owner rebind, exact-row safety, relation/constraint policy, and historical incident replay.
- `brand`: native Brand selection and Step2→Step3 transition recovery.
- `ownership`: Chromium target/page ownership and shared workflow state-machine contracts.
- `cdp`: Edge harness, lease contract, real cross-process serialization, token inheritance, and crash recovery.
- `schema`: live-schema and read-only planner contracts.
- `resolver`: AI resolver logic using existing mocks/fakes plus retry/transport contracts. No paid provider is reachable in Test Lab.
- `fill_plan`: deterministic Fill Plan logic.
- `batch`: Batch architecture, target ownership and Step1 transient recovery.
- `diagnostics`: failure diagnostic and FAILED telemetry completeness.

Targeted suites are for speed while modifying one subsystem. They are not the release gate. `--all` is the offline release gate because it includes every non-probe test in the repository, including tests not assigned to one of the targeted groups.

## Historical incidents become permanent regressions

`tests/fixtures/regressions/` stores minimized, non-live replay evidence for bugs that previously required a real run to expose.

The first fixture is `ultrasonic_cleaner_current_generation.json`, reconstructed from the 2026-08-26 failure telemetry. It deliberately tests only the browser-state ownership regression:

1. run the complete seven-query discovery ladder;
2. make the global decision;
3. observe that the selected historical row is already uniquely owned by the final active query;
4. click that current generation directly;
5. prove there is no eighth redundant `ultrasonic cleaner` query.

The fixture explicitly does **not** assert that the historical `Lens Cleaners` semantic choice was correct. Category-quality policy is a separate regression boundary.

When a new real incident reveals a root bug, minimize the state needed to reproduce that bug and add it here. A real failure should ideally be paid for once and replayed free forever afterward.

## CDP stress test

The `cdp` tests include real Python subprocesses; the OS file-lock boundary is not mocked. They verify:

- unrelated processes serialize on one CDP port;
- a synchronous child inherits the parent owner's cryptographic lease token;
- child release cannot release root ownership;
- an owner process can crash and the OS lock is still recoverable;
- stale owner metadata cannot become ownership authority.

Increase `--stress-rounds` when changing browser-session code. The accepted range is 1–100.

## What Test Lab cannot prove

Offline replay can catch deterministic code bugs, regressions, race/ownership contracts and most error-handling defects. It cannot prove that today's live Makro DOM, account session, network path or marketplace behaviour has not changed.

The intended release sequence is:

1. `python tools/test_lab.py --all`
2. if browser/session code changed: `python tools/test_lab.py --suite cdp --stress-rounds 25` (or higher)
3. fix every offline failure first
4. only then run one small real Makro/Amazon smoke test

Do not use expensive real supplier runs as the first debugging layer.

## Claude Code / local executor use

Claude Code is an execution-only local runner for this project. It may synchronize the dedicated temporary test clone, run Test Lab, and collect complete failure artifacts. It must not edit source code, tests, configuration, dependencies, or Git history, and it must not propose or apply fixes.

A useful instruction is:

> Synchronize the dedicated temporary test clone to the requested `feat/local-test-gui` HEAD and run `python tools/test_lab.py --all --json-report logs/test-lab/latest.json`. Do not run real Amazon/Makro workflows or use paid AI. Do not modify source code, tests, configuration, dependencies, or Git history. If tests fail, preserve the complete stdout/stderr and traceback, list every FAILED/ERROR node, and return those artifacts to ChatGPT for diagnosis and repair.

Manual browser interaction should be reserved for the final live smoke check or for capturing a new marketplace state that existing fixtures cannot represent.
