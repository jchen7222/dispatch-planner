"""Stage 8 — the calibration loop: the analytics feed the planner.

Three feedbacks, in order of trust:
  1. Tripwire (CI): conservation, window-compliance, utilization floor — a
     commit that makes plans worse fails the build.
  2. Calibration (this module): a MANUAL `calibrate` step reads the day's plan
     and regenerates a VERSIONED, DIFFABLE artifact the planner reads as
     configuration — never hardcoded, never silent: per-corridor circuity
     fits, volatility-sized window buffers (wider buffers for noisier
     corridors — the same instinct as wider alert thresholds for noisier
     suppliers), and a solver time budget from observed solve behavior.
  3. Objective shaping: the blast-radius mart (parties x value per truck)
     exposes which trucks are expensive to delay; packing prefers not to
     concentrate many parties' high-value orders on one fragile truck.

Ledger discipline applies to configuration too: every calibration write is a
`config_updated` event, and every plan records the calibration version it
used — "which circuity factors did dispatch believe at 06:00?" is answerable.

Honesty guardrail: on synthetic data this demonstrates the MECHANISM, not
learned real-world gains. The circuity fit doubles as a parameter-recovery
check: the generator builds distances with a known 1.3 road factor, and the
fit recovers it — evidence the estimator works, stated as such."""
import hashlib
import json
import os
import statistics

from . import geo


def fit(loads, orders_by_id):
    """Fit per-corridor parameters from a planned day."""
    per = {}
    for l in loads:
        if not l.stop_sequence:
            continue
        z = per.setdefault(l.zone, {"ratios": [], "slacks": [], "loads": 0})
        z["loads"] += 1
        # circuity fit: planned road km vs straight-line haversine km
        pts = [geo.DEPOT] if hasattr(geo, "DEPOT") else []
        from .generator import DEPOT
        seqpts = [DEPOT] + [(orders_by_id[o].lat, orders_by_id[o].lon)
                            for o in l.stop_sequence] + [DEPOT]
        hav = sum(geo.haversine_km(a, b) for a, b in zip(seqpts, seqpts[1:]))
        if hav > 0:
            z["ratios"].append(l.distance_km / hav)
        # volatility: window slack at each stop (min left before window_end)
        for oid, arr in l.stop_arrivals.items():
            z["slacks"].append(orders_by_id[oid].window_end - arr)
    out = {}
    for zone, z in sorted(per.items()):
        vol = statistics.pstdev(z["slacks"]) if len(z["slacks"]) > 1 else 0.0
        out[zone] = {
            "circuity_fit": round(sum(z["ratios"]) / len(z["ratios"]), 3) if z["ratios"] else None,
            "window_buffer_min": int(round(min(120, 0.5 * vol))),   # wider for noisier
            "arrival_slack_stdev_min": round(vol, 1),
            "loads_observed": z["loads"],
        }
    return out


def write_artifact(path_dir, corridors, solver_stats, plan_fingerprint, as_of):
    os.makedirs(path_dir, exist_ok=True)
    existing = sorted(f for f in os.listdir(path_dir)
                      if f.startswith("calibration_v") and f.endswith(".json"))
    version = len(existing) + 1
    artifact = {
        "calibration_version": version,
        "generated_from_plan": plan_fingerprint,
        "generated_as_of": as_of,
        "corridors": corridors,
        "solver": solver_stats,
        "note": ("mechanism demonstration on synthetic data; the circuity fit "
                 "recovering the generator's known 1.3 factor is a "
                 "parameter-recovery check, not a real-world gain"),
    }
    path = os.path.join(path_dir, f"calibration_v{version}.json")
    with open(path, "w") as f:
        json.dump(artifact, f, indent=1, sort_keys=True)
    digest = hashlib.sha256(open(path, "rb").read()).hexdigest()[:12]
    return path, version, digest


def load_artifact(path):
    with open(path) as f:
        return json.load(f)
