# Ecommerce-Agent Test Lab

`tools/test_lab.py` is the default developer regression entrypoint for changes that should not spend AI quota or require a real supplier listing run.

## Fast start

Run every curated offline suite:

```bash
python tools/test_lab.py --all
```

Run one area repeatedly while developing:

```bash
python tools/test_lab.py --suite vertical
python tools/test_lab.py --suite cdp --stress-rounds 25
python tools/test_lab.py --suite resolver --suite fill_plan
```

List suites:

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
- HTTP(S) proxy variables inherited by nested child processes point to a closed loopback port;
- loopback remains available for local process/CDP coordination tests;
- curated suites do not execute real Amazon capture or real Makro listing writes;
- Test Lab never clicks Save or Send to QC.

If a test unexpectedly tries to escape to the network it should fail with `TEST_LAB_EXTERNAL_NETWORK_BLOCKED` instead of silently spending quota.

## Suite map

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

The `cdp` suite includes real Python subprocesses; it does not mock the OS file lock. It verifies:

- unrelated processes serialize on one CDP port;
- a synchronous child inherits the parent owner's cryptographic lease token;
- child release cannot release root ownership;
- an owner process can crash and the OS lock is still recoverable;
- stale owner metadata cannot become ownership authority.

Increase `--stress-rounds` when changing browser-session code. The accepted range is 1–100.

## What Test Lab cannot prove

Offline replay can catch deterministic code bugs, regressions, race/ownership contracts and most error-handling defects. It cannot prove that today's live Makro DOM, account session, network path or marketplace behaviour has not changed.

The intended release sequence is therefore:

1. `python tools/test_lab.py --all`
2. targeted high-round stress for the area changed, if applicable
3. only after those pass, one small real Makro/Amazon smoke run

Do not use expensive real supplier runs as the first debugging layer.

## Claude Code / local agent use

A local coding agent should run Test Lab rather than manually clicking through the whole product flow. A useful instruction is:

> Run `python tools/test_lab.py --all`. Do not run real Amazon/Makro workflows or use paid AI. Fix root causes for any failing offline regression, rerun only the affected suite, then rerun all suites.

Manual browser interaction should be reserved for the final live smoke check or for capturing a new marketplace state that cannot be represented by existing fixtures.
