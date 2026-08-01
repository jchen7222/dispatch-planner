"""Pipeline entry point.  python -m dispatch.run --seed 42 [--orders 80] [--disrupt]"""
import argparse, hashlib, json, os
from .generator import make_orders, make_drivers, FLEET
from .packing import pack
from .routing import route_load
from .assignment import assign
from .ledger import Ledger
from .models import Exception_
from . import metrics
from .delay import inject_delay, render, live_notifications

CUTOFF = 240          # 04:00 planning cutoff: event_time for the morning plan

def plan(seed=42, n_orders=80, ledger_path=None, calibration=None):
    orders = make_orders(n_orders, seed)
    drivers = make_drivers(20, seed)
    led = Ledger(ledger_path)
    for o in orders:
        led.append("order_placed", o.order_id, {"zone": o.zone, "kg": o.weight_kg},
                   event_time=CUTOFF - 60, record_time=CUTOFF - 60)
    loads, exceptions = pack(orders, FLEET, calibration=calibration)
    omap = {o.order_id: o for o in orders}
    routed = []
    for l in loads:
        err = route_load(l, omap, calibration=calibration)
        if err:
            exceptions.extend(Exception_(a.order_id, "window_infeasible")
                              for a in l.allocations)
        else:
            routed.append(l)
    exceptions.extend(assign(routed, drivers, omap))
    for l in routed:
        led.append("load_plan_created", l.load_id, {
            "model": l.model.name, "driver": l.driver_id,
            "orders": [a.order_id for a in l.allocations],
            "depart": l.depart_min, "back": l.arrive_back_min,
            "calibration_version": (calibration or {}).get("calibration_version", 0),
        }, event_time=CUTOFF, record_time=CUTOFF)
    for e in exceptions:
        led.append("order_excepted", e.order_id, {"reason": e.reason},
                   event_time=CUTOFF, record_time=CUTOFF)
    return orders, drivers, routed, exceptions, led

def fingerprint(loads):
    blob = json.dumps([[l.load_id, l.model.name, l.driver_id, l.stop_sequence]
                       for l in sorted(loads, key=lambda x: x.load_id)]).encode()
    return hashlib.sha256(blob).hexdigest()[:12]

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--orders", type=int, default=80)
    ap.add_argument("--calibrate", action="store_true",
                    help="after planning, fit per-corridor parameters from the "
                         "plan and write a versioned calibration artifact "
                         "(+ config_updated ledger event)")
    ap.add_argument("--calibration", default=None,
                    help="path to a calibration_vN.json the planner should use")
    ap.add_argument("--disrupt", action="store_true",
                    help="cancel one planned order after cutoff and show the compensating event")
    args = ap.parse_args()

    calib = None
    if args.calibration:
        from .calibration import load_artifact
        calib = load_artifact(args.calibration)
    orders, drivers, loads, exceptions, led = plan(args.seed, args.orders,
                                                   calibration=calib)
    m = metrics.build(loads, orders, exceptions, FLEET)

    print(f"plan fingerprint : {fingerprint(loads)}"
          + (f"   (calibration v{calib['calibration_version']})" if calib else ""))
    planned_ids = {a.order_id for l in loads for a in l.allocations}
    excepted_orders = {e.order_id for e in exceptions if e.order_id.startswith("O")}
    print(f"orders           : {len(orders)}   planned: {len(planned_ids)}   excepted: {len(excepted_orders)}")
    print(f"trucks used      : {m['trucks_used']}  (naive one-order-one-truck baseline: {m['naive_trucks']})")
    print(f"drivers used     : {m['drivers_used']}   total distance: {m['total_km']} km")
    print(f"OTIF w/ delays   : {m['otif_pct_with_injected_delays']}%   by value: {m['otif_by_value_pct']}%")
    br = m["blast_radius_top5"][0] if m["blast_radius_top5"] else None
    if br:
        print(f"blast radius     : widest truck {br[0]} touches {br[1]} parties, ${br[2]:,.0f} at risk")
    print("utilization      : model | trucks | avg fill (binding dim) | weigh-out | cube-out")
    for row in m["util"]:
        print(f"                   {row[0]:<8} | {row[1]:>5}  | {row[2]:>8}              | {row[3]:>6}   | {row[4]}")
    if m["exceptions"]:
        print("exceptions       :", ", ".join(f"{r}={c}" for r, c in m["exceptions"]))

    if args.calibrate and loads:
        path, version = run_calibrate(loads, orders, led, fingerprint(loads), CUTOFF + 300)
        print(f"calibration      : wrote v{version} -> {os.path.relpath(path)} "
              f"(config_updated event appended; plans record the version they use)")

    if args.disrupt and loads:
        with_driver = [l for l in loads if l.driver_id and l.stop_sequence]
        victim = with_driver[0] if with_driver else loads[0]
        oid = victim.stop_sequence[-1]
        plan_ev = next(e for e in led.events
                       if e["type"] == "load_plan_created" and e["entity_id"] == victim.load_id)
        led.append("order_cancelled", oid, {"reason": "customer_cancel"},
                   event_time=CUTOFF + 90, record_time=CUTOFF + 95)
        led.append("plan_amended", victim.load_id, {
            "model": victim.model.name, "driver": victim.driver_id,
            "orders": [a.order_id for a in victim.allocations if a.order_id != oid],
            "depart": victim.depart_min, "back": victim.arrive_back_min,
            "note": f"compensates cancellation of {oid}",
        }, event_time=CUTOFF + 95, record_time=CUTOFF + 100, corrects=plan_ev["seq"])
        before = led.fold_current(as_of_record_time=CUTOFF)[victim.load_id]["payload"]["orders"]
        after = led.fold_current()[victim.load_id]["payload"]["orders"]
        print(f"\ndisruption demo  : {oid} cancelled at T+95")
        print(f"  as-of cutoff   : {victim.load_id} carried {before}")
        print(f"  current        : {victim.load_id} carries {after}")
        print(f"  original event untouched — superseded by compensating event (append-only)")

        others = [l for l in loads if l.driver_id and len(l.stop_sequence) >= 2
                  and l is not victim]
        if others:
            tgt = others[0]
            omap2 = {o.order_id: o for o in orders}
            notes = inject_delay(tgt, 300, omap2, led, event_time=600, record_time=605)
            print(f"\ndelay demo       : {tgt.load_id} delayed +300 min — "
                  f"{len(notes)} party notification(s) queued (delay measured vs the promise)")
            for n in notes[:2]:
                print("  " + render(n).replace("\n", "\n  "))
            inject_delay(tgt, 180, omap2, led, event_time=700, record_time=705)
            live = [n for n in live_notifications(led)
                    if n["payload"]["truck"] == tgt.load_id]
            total = [e for e in led.events if e["type"] == "delay_notification_queued"
                     and e["payload"]["truck"] == tgt.load_id]
            print(f"  second delay +180: {len(total)} notifications in audit trail, "
                  f"{len(live)} live — superseded, never duplicated")

def run_calibrate(loads, orders, led, fingerprint_str, as_of):
    from .calibration import fit, write_artifact
    omap = {o.order_id: o for o in orders}
    corridors = fit([l for l in loads if l.stop_sequence], omap)
    path, version, digest = write_artifact(
        os.path.join(os.path.dirname(__file__), "..", "calibration"),
        corridors, {"assign_time_budget_s": 10}, fingerprint_str, as_of)
    led.append("config_updated", f"calibration_v{version}", {
        "version": version, "sha256_12": digest, "path": os.path.basename(path),
        "corridors": corridors,
    }, event_time=as_of, record_time=as_of)
    return path, version


if __name__ == "__main__":
    main()
