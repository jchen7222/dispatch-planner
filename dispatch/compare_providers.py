"""The Stage-3A README demo: the same day planned under haversine vs. real
road times (OSRM), with the plan delta and the measured API-to-haversine bias
per corridor — the API integration and the reason calibration exists, in one
table.

  python -m dispatch.compare_providers --seed 42 --orders 30 --record   # live, writes cassette
  python -m dispatch.compare_providers --seed 42 --orders 30            # replay (CI-safe)"""
import argparse
import json
import os

from .generator import make_orders, make_departures, SERVICES
from .packing import pack
from .routing import route_load
from .assignment import assign
from .providers import HaversineProvider, OSRMProvider, ResilientClient
from .run import fingerprint

CASSETTE = os.path.join(os.path.dirname(__file__), "..", "fixtures",
                        "cassettes", "osrm_table.json")


def plan_with(provider, seed, n_orders):
    orders = make_orders(n_orders, seed)
    departures = make_departures(seed=seed)
    loads, exceptions = pack(orders, SERVICES)
    omap = {o.order_id: o for o in orders}
    routed = [l for l in loads if route_load(l, omap, provider=provider) is None]
    assign(routed, departures, omap)
    return routed


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--orders", type=int, default=30)
    ap.add_argument("--record", action="store_true",
                    help="call the live OSRM server once and write the cassette")
    args = ap.parse_args()

    hav = HaversineProvider()
    if args.record:
        client = ResilientClient("osrm", record_to=CASSETTE, rate_limit_s=1.2)
    else:
        cassette = json.load(open(CASSETTE))
        client = ResilientClient("osrm", cassette=cassette)
    osrm = OSRMProvider(client=client, fallback=hav)

    base = plan_with(hav, args.seed, args.orders)
    road = plan_with(osrm, args.seed, args.orders)
    if args.record:
        client.save_cassette()
        print(f"cassette written: {os.path.relpath(CASSETTE)} ({len(client.recorded)} responses)")

    def tot(loads, attr):
        return sum(getattr(l, attr) for l in loads)

    print(f"{'':22}{'haversine':>12}{'osrm (real roads)':>20}")
    print(f"{'consignments used':22}{len(base):>12}{len(road):>20}")
    print(f"{'total distance km':22}{tot(base,'distance_km'):>12.1f}{tot(road,'distance_km'):>20.1f}")
    print(f"{'total drive min':22}{tot(base,'drive_min'):>12}{tot(road,'drive_min'):>20}")
    print(f"{'plan fingerprint':22}{fingerprint(base):>12}{fingerprint(road):>20}")
    print(f"{'degraded?':22}{'-':>12}{str(osrm.degraded):>20}")

    # measured bias: OSRM time / haversine time per corridor — the number the
    # calibration loop would correct (API improves the mean, not the variance)
    by_zone = {}
    for b, r in zip(sorted(base, key=lambda l: l.load_id),
                    sorted(road, key=lambda l: l.load_id)):
        if b.zone == r.zone and b.drive_min:
            by_zone.setdefault(b.zone, []).append(r.drive_min / b.drive_min)
    print("\nmeasured bias (osrm/haversine drive-time ratio) per corridor:")
    for z, ratios in sorted(by_zone.items()):
        print(f"  {z:6} x{sum(ratios)/len(ratios):.2f}   ({len(ratios)} loads)")


if __name__ == "__main__":
    main()
