# Load Consolidation & Carrier Assignment

Every morning a cross-border forwarder faces the same puzzle: a pile of orders bound overseas, a set of carrier services with different capabilities and prices, promised delivery dates, and goods that may not all ride the same service. Perfume is a flammable liquid under IATA; lithium cells are dangerous goods under UN3480/3481; a 40 kg carton of cosmetics and a 2 kg one do not go the same way. This project is that forwarder, automated. It decides **which goods may legally ride which carrier**, which orders consolidate so the fewest consignments are booked, in what sequence the collection stops happen so every window is kept, and which departure each consignment is tendered to before its cutoff. Every routing decision is emitted as an event carrying **the rule-set version and the rule id that produced it**, so a mis-route six weeks and four rule edits later is a two-line query, not an afternoon of reconstruction. When a pickup is missed, a booking is cancelled or a lane goes down, nothing is erased: the change is recorded, the plan recalculates, and the ledger shows exactly what the plan said at any moment. When a delay breaks a promise, the system traces which orders, suppliers and customers are hit and drafts each party's notification. A metrics layer keeps score, and automated checks guarantee the three things that matter: no order is lost, no affected party goes un-notified, and **no restricted commodity ever rides a service that is not approved to carry it**.

> **All order, carrier, party, and departure data in this repository is synthetic**, produced by a seeded generator (`dispatch/generator.py`). The *methods* are the real ones the industry uses; the data is not real and is not presented as real.

## Quickstart

```bash
pip install -r requirements.txt
python -m pytest -q                    # 36 property tests
python -m dispatch.run --seed 42 --disrupt
```

## One run, real output

```
eligibility      : rule set v1 (2026-09-01) — cosmetics 12 · flammable_liquid 2 · general 61 · lithium_battery 5   refused by rule: 0
plan fingerprint : a5e5c323b86a
orders           : 80   planned: 79   excepted: 1
consignments used      : 37  (naive one-order-one-consignment baseline: 85)
departures used     : 19   total distance: 7233.2 km
OTIF w/ delays   : 84.3%   by value: 94.3%
blast radius     : widest consignment L026 touches 6 parties, $191,859 at risk
utilization      : service | consignments | avg fill (binding dim) | weigh-out | cube-out
                   DGExpress |    20  |    0.619              |     14   | 6
                   Economy  |     3  |    0.753              |      3   | 0
                   Standard |    14  |    0.827              |     10   | 4
exceptions       : indivisible_oversize=1
```

Consolidation cuts the booking from a naive 85 consignments to 37 — **56% fewer consignments** — with one honest exception: an order physically too big for any truck and indivisible. Nothing is dropped silently — every unplanned order carries a reason code, and every broken promise produces exactly one live notification per affected party.

## The methods, by name

| Stage | Problem class | Method | Where |
|---|---|---|---|
| **Carrier eligibility** | **Rule evaluation over a versioned rule set** | **capability ∩ business rule; first match wins; decision emitted with `rule_set_version` + `matched_rule`** | `eligibility.py` |
| Consolidation | Bin packing (2-dim: weight + cube) | **First-Fit Decreasing** — provably ≤ 11/9·OPT + 6/9 bins | `packing.py` |
| Oversize orders | Split Delivery VRP idea | split across trucks only when `divisible=True`; else exception | `packing.py` |
| Service choice | Heterogeneous carrier set | smallest **eligible** service that fits → best projected fill, cost tie-break | `packing.py` |
| Stop sequencing | **VRPTW** (single-vehicle per load) | OR-Tools `RoutingModel`, `PATH_CHEAPEST_ARC`, deterministic | `routing.py` |
| Departure flexibility | bitemporal route timing | solved **twice**: earliest departure and just-in-time departure → the pair becomes the decision space | `routing.py` |
| Departure assignment | Interval scheduling | **CP-SAT**: optional intervals, `NoOverlap` per departure slot, tender accepted only inside the acceptance window and before cutoff | `assignment.py` |
| Plan of record | Event sourcing | append-only ledger; corrections are **compensating events**; point-in-time folds | `ledger.py` |
| Delay impact | **Lineage traversal + idempotent fan-out** | consignment → stops → orders → parties; scoped notification per party; supersede by (party × truck), never duplicate | `delay.py` |
| Analytics | — | DuckDB: utilization + binding dimension, naive-baseline savings, OTIF under injected delay, exceptions | `metrics.py` |

## The carrier network is configuration, not code

Every carrier service, what it is **approved to carry**, the corridors, the handling limits, **and the routing rules** live in **`config/network.yml`**. Onboarding a carrier is a pull request that touches one YAML entry and zero Python files; so is changing which goods may ride it. CI proves both: `tests/test_config.py` instantiates a network from a fixture template carrying an extra service (`MiniParcel`) and its own rule set (`alt-v1`), and packs orders onto it with no code change.

Eligibility is the **intersection of two independent facts**: what the carrier is certified to accept (`approved:`, a fact about the carrier) and what we are willing to send there (`routing_rules:`, which may be stricter). A rule that allows a service the carrier is not approved for changes nothing — tested.

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

## Terminology

Chinese forwarding says 敏感货 (sensitive goods) and 敏感线 (sensitive line). The English trade does not use "sensitive". Carriers publish **prohibited** (never carried) and **restricted** (carried only under conditions) lists, and a service is **approved**, or not approved, for a commodity class.

| 中文 | this codebase |
|---|---|
| 普货 | `general` |
| 敏感货 | a restricted `commodity_class` |
| 敏感线 / 专线 | a service whose `approved:` list includes that class |
| 违禁品 | `prohibited` — rule `R1`, refused before any lane is considered |
| 带电 / 含酒精 | `lithium_battery` / `flammable_liquid`, both dangerous goods |

`lane` is correct English — one origin-destination pair with a service.

## Traps that are content, not obstacles

- **Silent infeasibility is forbidden.** Every unplanned order becomes an exception event with a reason code. The conservation test enforces it.
- **Weight vs. cube:** which capacity binds depends on freight density — each truck reports *weigh-out* or *cube-out*.
- **Determinism:** fixed seeds, single solver worker, no time-based metaheuristic on the CI path. The plan fingerprint is asserted stable.
- **Waiting time is not driving time:** HOS uses pure drive minutes; the span a driver occupies is modeled separately (and shrinks when departing later).
- **Delay math against the PROMISE, not the plan.** A truck can run late and still deliver inside the window — that is a delay of zero and **no notification**. Getting this wrong spams parties about non-events (tested).
- **Scope the notification to the party.** A supplier never sees another party's orders, amounts, or value — data scoping is a correctness property here (tested).
- **Supersede, never duplicate.** A truck delayed twice updates its notification (idempotency key: party × truck); the ledger keeps both — the audit trail shows what each party was told at every point (tested).
- **Travel times** are haversine × 1.3 road-circuity ÷ 60 km/h — the standard first approximation; swapping in OSRM road times is a one-module change (`geo.py`).

## The 36 property tests

conservation (orders in = planned + excepted) · split allocations sum back to order mass · capacity never exceeded · every arrival inside its window · HOS and shift legality, no driver overlap · divisible oversize splits / indivisible excepts · same seed ⇒ same plan fingerprint · utilization floor vs. naive baseline · **notification coverage** (every late order in exactly one live notification per affected party) · **no cross-party leakage** · **on-time orders are non-events** · **second delay supersedes, never duplicates, audit trail preserved** · **delay inside the window notifies no one** · circuity parameter recovery (fit = 1.3) · wider buffers for noisier corridors · versioned artifact + honesty note · plans record their calibration version · blast-radius preference soft but real · network instantiates from a fixture template (MiniParcel) · new service packs with zero code changes · **flammable liquid and lithium cells confine to the DG-approved service** · **a weight band narrows the services for cosmetics** · **prohibited goods have no lane and are refused by rule id** · **an unknown commodity class is refused by name, not by silence** · **carrier capability overrides a permissive rule** · **a refused parcel is an exception naming the rule, never a silent drop** · **the decision event carries the rule-set version** · **a past decision stays explainable after the rules change** · **blast radius is a query over the rule that fired** · retry-then-success with exponential backoff · circuit opens and the provider degrades to haversine (planning never stops) · cassette replay is deterministic and offline (a miss raises, never goes live)

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

## Licence

MIT — see [LICENSE](LICENSE). The engine is public; the rate cards, real lane rules and customer data that make it a business are not, and live outside this repository by design (`config/` here carries synthetic values only).
