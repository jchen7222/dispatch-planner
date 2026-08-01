# Load Consolidation & Dispatch Planner

Every morning a dispatcher faces the same puzzle: a pile of orders, a mixed fleet, promised delivery windows, and drivers who may only legally drive so many hours — and a day that will not go according to plan. This project is that dispatcher, automated. It decides which orders ride together so the fewest trucks leave the yard, in what sequence the stops happen so every window is kept, and which driver takes which route so no one runs out of legal hours. When a truck breaks down, runs late, or a customer cancels, nothing is erased and nothing crashes: the change is recorded, the plan recalculates, and the ledger can show you exactly what the plan said at any moment before or after. When a delay actually breaks a promise, the system traces exactly which orders, suppliers, and customers are hit and drafts each party's notification — their order numbers, their amounts, their value, their days of delay — so nobody finds out from an empty dock. A metrics layer keeps score — how full the trucks ran, how many trucks the optimizer saved, how often deliveries landed on time and in full — and automated checks guarantee the two things a shipper cares about most: no order is ever lost, and no affected party goes un-notified.

> **All order, fleet, party, and driver data in this repository is synthetic**, produced by a seeded generator (`dispatch/generator.py`). The *methods* are the real ones the industry uses; the data is not real and is not presented as real.

## Quickstart

```bash
pip install -r requirements.txt
python -m pytest -q                    # 13 property tests
python -m dispatch.run --seed 42 --disrupt
```

## One run, real output

```
plan fingerprint : afa51301b20a
orders           : 80   planned: 79   excepted: 1
trucks used      : 37  (naive one-order-one-truck baseline: 85)
drivers used     : 20   total distance: 7168.4 km
OTIF w/ delays   : 84.3%   by value: 85.2%
blast radius     : widest truck L021 touches 8 parties, $137,679 at risk
utilization      : model | trucks | avg fill (binding dim) | weigh-out | cube-out
                   BoxTruck |    18  |    0.825              |     15   | 3
                   Semi     |    14  |    0.818              |      8   | 6
                   Sprinter |     5  |    0.615              |      5   | 0
exceptions       : indivisible_oversize=1

disruption demo  : O0069 cancelled at T+95
  as-of cutoff   : L001 carried ['O0031', 'O0069', 'O0039']
  current        : L001 carries ['O0031', 'O0039']
  original event untouched — superseded by compensating event (append-only)

delay demo       : L005 delayed +300 min — 4 party notification(s) queued (delay measured vs the promise)
  NOTIFY C04 (Customer 04) — truck L005 delayed:
    O0045 (customer): 56 units, $3,486.80 — 1 day(s) late (117 min past window)
    total value at delay: $3,486.80
  NOTIFY C06 (Customer 06) — truck L005 delayed:
    O0002 (customer): 50 units, $3,325.92 — 1 day(s) late (270 min past window)
    total value at delay: $3,325.92
  second delay +180: 8 notifications in audit trail, 4 live — superseded, never duplicated
```

Consolidation cuts the fleet from a naive 85 trucks to 37 — **56% fewer trucks** — with one honest exception: an order physically too big for any truck and indivisible. Nothing is dropped silently — every unplanned order carries a reason code, and every broken promise produces exactly one live notification per affected party.

## The methods, by name

| Stage | Problem class | Method | Where |
|---|---|---|---|
| Consolidation | Bin packing (2-dim: weight + cube) | **First-Fit Decreasing** — provably ≤ 11/9·OPT + 6/9 bins | `packing.py` |
| Oversize orders | Split Delivery VRP idea | split across trucks only when `divisible=True`; else exception | `packing.py` |
| Fleet choice | Heterogeneous fleet | smallest fitting model → best projected fill, cost tie-break | `packing.py` |
| Stop sequencing | **VRPTW** (single-vehicle per load) | OR-Tools `RoutingModel`, `PATH_CHEAPEST_ARC`, deterministic | `routing.py` |
| Departure flexibility | bitemporal route timing | solved **twice**: earliest departure and just-in-time departure → the pair becomes the decision space | `routing.py` |
| Driver assignment | Interval scheduling + rostering | **CP-SAT**: optional intervals, `NoOverlap` per driver, simplified FMCSA HOS (11h driving / 14h duty) | `assignment.py` |
| Plan of record | Event sourcing | append-only ledger; corrections are **compensating events**; point-in-time folds | `ledger.py` |
| Delay impact | **Lineage traversal + idempotent fan-out** | truck → stops → orders → parties; scoped notification per party; supersede by (party × truck), never duplicate | `delay.py` |
| Analytics | — | DuckDB: utilization + binding dimension, naive-baseline savings, OTIF under injected delay, exceptions | `metrics.py` |

## The fleet is configuration, not code

Every truck model, the depot, the corridors, and the HOS parameters live in **`config/fleet.yml`**. Adding a truck model is a pull request that touches one YAML entry and zero Python files — and CI proves it: `tests/test_config.py` instantiates a fleet from a fixture template with an extra model (`CargoBike`) and packs orders onto it with no code change. A truck model is an asset class; onboarding one is a template definition — configuration-over-code, the same principle as the calibration artifact below.

## Stage 3A — travel times as a pluggable provider (the API-integration layer)

The travel-time matrix sits behind a `TravelTimeProvider` interface (`dispatch/providers.py`): **HaversineProvider** (zero dependencies, the default and the degrade target), **OSRMProvider** (real road times, keyless), and **GoogleMatrixProvider** (Distance Matrix API, key via env var, never committed). Live calls run through `ResilientClient` — retry with exponential backoff, rate limiting, response caching, and a **circuit breaker that degrades gracefully to haversine** when the API is down: planning never stops, and the provider flags itself `degraded`. CI never touches a live API — a **record/replay cassette** (`fixtures/cassettes/osrm_table.json`, 18 REAL responses recorded from the public OSRM server, August 2026) replays deterministically, and a cassette miss raises instead of silently going live.

The demo (`python -m dispatch.compare_providers`): the same 30-order day planned twice —

```
                         haversine   osrm (real roads)
trucks used                     18                  18
total distance km           3208.0              3340.9
total drive min               3208                3012
plan fingerprint      92b64039f7d8        91ad31f08a1d

measured bias (osrm/haversine drive-time ratio) per corridor:
  EAST   x0.82    NE     x1.09    NORTH  x0.90
  SOUTH  x0.88    SW     x1.01    WEST   x0.83
```

Real roads run *faster* than haversine×1.3 on highway corridors (EAST 0.82) and *slower* in NE (1.09) — a per-corridor bias no global constant can fix, which is exactly why the calibration loop exists. With a live API, calibration does not retire — its targets shift: the API improves the **mean** (so hand-set circuity factors retire in favor of API-to-actual bias correction), but not the **variance** (so volatility-sized window buffers survive untouched), and solver budgets and blast-radius shaping never depended on travel data at all.

## Stage 8 — the calibration loop (the analytics feed the planner)

The marts are not just a scoreboard; the loop closes three ways. **Tripwire:** conservation, window-compliance, and the utilization floor run in CI — a commit that makes plans worse fails the build. **Calibration:** `python -m dispatch.run --seed 42 --calibrate` fits per-corridor parameters from the day's plan and writes a **versioned, diffable artifact** (`calibration/calibration_vN.json`) that the planner reads back with `--calibration <path>`: per-corridor circuity factors, and **window buffers sized to arrival-slack volatility — wider buffers for noisier corridors** (the same instinct as wider alert thresholds for noisier suppliers). Every calibration write is a `config_updated` ledger event and **every plan records the calibration version it used** — "which circuity factors did dispatch believe at 06:00?" is answerable, and any historical plan reproduces exactly. **Objective shaping:** the blast-radius mart (parties × value per truck) feeds a *soft* packing preference that avoids concentrating many parties' high-value orders on one fragile truck, and OTIF is reported by value as well as by stop.

Honesty guardrail, in the artifact itself: on synthetic data this demonstrates the **mechanism**, not learned real-world gains — and the circuity fit doubles as a **parameter-recovery check**: the generator builds distances with a known 1.3 road factor, and the fit recovers 1.300 per corridor, which is evidence the estimator works, stated as exactly that. `calibrate` stays a manual step producing a diffable file, so CI stays deterministic and every tuning is visible in a diff, never silent.

## A finding the architecture surfaced

Two departure policies, same orders, same fleet:

- **Earliest departure** absorbs injected delays completely (OTIF 100%) — waiting time at stops doubles as buffer — but the long spans left a third of loads without a legal driver.
- **Just-in-time departure** (what the CP-SAT stage chooses when it helps) gets every load a driver — spans shrink toward the waitless minimum so drivers chain routes — but the buffer is gone: the same injection drops OTIF to ~84%.

That is the safety-stock trade-off (buffer sized to variability) appearing in the time dimension, and it fell out of the model rather than being asserted. The dial is `latest_depart` in `routing.py`.

## Traps that are content, not obstacles

- **Silent infeasibility is forbidden.** Every unplanned order becomes an exception event with a reason code. The conservation test enforces it.
- **Weight vs. cube:** which capacity binds depends on freight density — each truck reports *weigh-out* or *cube-out*.
- **Determinism:** fixed seeds, single solver worker, no time-based metaheuristic on the CI path. The plan fingerprint is asserted stable.
- **Waiting time is not driving time:** HOS uses pure drive minutes; the span a driver occupies is modeled separately (and shrinks when departing later).
- **Delay math against the PROMISE, not the plan.** A truck can run late and still deliver inside the window — that is a delay of zero and **no notification**. Getting this wrong spams parties about non-events (tested).
- **Scope the notification to the party.** A supplier never sees another party's orders, amounts, or value — data scoping is a correctness property here (tested).
- **Supersede, never duplicate.** A truck delayed twice updates its notification (idempotency key: party × truck); the ledger keeps both — the audit trail shows what each party was told at every point (tested).
- **Travel times** are haversine × 1.3 road-circuity ÷ 60 km/h — the standard first approximation; swapping in OSRM road times is a one-module change (`geo.py`).

## The 23 property tests

conservation (orders in = planned + excepted) · split allocations sum back to order mass · capacity never exceeded · every arrival inside its window · HOS and shift legality, no driver overlap · divisible oversize splits / indivisible excepts · same seed ⇒ same plan fingerprint · utilization floor vs. naive baseline · **notification coverage** (every late order in exactly one live notification per affected party) · **no cross-party leakage** · **on-time orders are non-events** · **second delay supersedes, never duplicates, audit trail preserved** · **delay inside the window notifies no one** · circuity parameter recovery (fit = 1.3) · wider buffers for noisier corridors · versioned artifact + honesty note · plans record their calibration version · blast-radius preference soft but real · fleet instantiates from a fixture template (CargoBike) · new model packs with zero code changes · retry-then-success with exponential backoff · circuit opens and the provider degrades to haversine (planning never stops) · cassette replay is deterministic and offline (a miss raises, never goes live)

## Own-it exercises (in order)

1. Run it. Change the seed. Watch the exception mix change. Explain why.
2. Break `test_conservation` on purpose (drop an exception append), watch CI catch it, revert.
3. Build the before/after picture from the metrics: naive dispatch (one order, one truck) beside the consolidated plan (fill bars), and one delivery flipping late→on-time after a re-plan. One matplotlib module — it is the value proposition in a single image.
4. Run `--calibrate`, read the diff between v1 and a v2 fitted from a different seed, and explain each changed number.
5. Add a `--depart-policy earliest|jit` flag to `run.py` and reproduce the OTIF trade-off table yourself.
6. Port `metrics.py` views to dbt models with schema tests (dbt-duckdb) — including the conservation and notification-coverage assertions as dbt tests.
7. Add a second depot and make `pack()` depot-aware.
8. Point `OSRM_URL` at a self-hosted OSRM instance and re-record the cassette; compare the bias table against the public server's.
9. Add a `GoogleMatrixProvider` cassette: get a key, record one day, and extend `compare_providers` to a three-way table.

## Publishing

```bash
git init && git add -A && git commit -m "dispatch planner: FFD + VRPTW + CP-SAT over an event ledger, with delay-impact notifications"
gh repo create dispatch-planner --public --source=. --push
```

Commits will be dated when you make them — the project is honestly a 2026 project, and its date should say so.
