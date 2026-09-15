"""Stage 5A properties: coverage, scoping, promise-not-plan, supersede-not-duplicate."""
import pytest
from dispatch.run import plan
from dispatch.delay import inject_delay, live_notifications
from dispatch.models import Order, Load, Allocation
from dispatch.generator import SERVICES
from dispatch.ledger import Ledger

SEED = 42


@pytest.fixture()
def delayed():
    orders, departures, loads, exceptions, led = plan(seed=SEED, n_orders=80)
    omap = {o.order_id: o for o in orders}
    target = next(l for l in loads if l.departure_id and len(l.stop_sequence) >= 2)
    notes = inject_delay(target, 300, omap, led, event_time=600, record_time=605)
    return omap, target, led, notes


def _late_orders(target, omap):
    return {oid for oid, eta in target.stop_arrivals.items()
            if eta > omap[oid].window_end}


def test_coverage_every_late_order_notified(delayed):
    omap, target, led, _ = delayed
    late = _late_orders(target, omap)
    live = live_notifications(led)
    for oid in late:
        o = omap[oid]
        for party in (o.supplier_id, o.customer_id):
            hits = [n for n in live if n["payload"]["party"] == party
                    and any(r["order_id"] == oid for r in n["payload"]["orders"])]
            assert len(hits) == 1, f"{oid} must appear in exactly one live notification for {party}"


def test_scoping_no_cross_party_leakage(delayed):
    omap, target, led, _ = delayed
    for n in live_notifications(led):
        party = n["payload"]["party"]
        for r in n["payload"]["orders"]:
            o = omap[r["order_id"]]
            assert party in (o.supplier_id, o.customer_id), \
                "a party must never see another party's orders"


def test_on_time_orders_not_notified(delayed):
    omap, target, led, _ = delayed
    late = _late_orders(target, omap)
    for n in live_notifications(led):
        for r in n["payload"]["orders"]:
            assert r["order_id"] in late, "delay is vs the promise — on-time orders are non-events"


def test_second_delay_supersedes_not_duplicates(delayed):
    omap, target, led, first_notes = delayed
    before_live = {n["entity_id"] for n in live_notifications(led)}
    inject_delay(target, 180, omap, led, event_time=700, record_time=705)
    live = live_notifications(led)
    per_key = {}
    for n in live:
        per_key[n["entity_id"]] = per_key.get(n["entity_id"], 0) + 1
    assert all(v == 1 for v in per_key.values()), "exactly one live notification per party x consignment"
    # history preserved: superseded events still in the ledger
    all_notes = [e for e in led.events if e["type"] == "delay_notification_queued"]
    assert len(all_notes) > len(live), "superseded notifications remain in the audit trail"


def test_delay_inside_window_is_a_non_event():
    o = Order("OX", "NORTH", 43.5, -76.1, 100, 1.0, 300, 1200, False,
              supplier_id="S00", customer_id="C00", amount=10, value_usd=500.0)
    ld = Load("LX", SERVICES[0], "NORTH", [Allocation("OX", 100, 1.0)], 300, 1200)
    ld.stop_sequence = ["OX"]
    ld.stop_arrivals = {"OX": 400}
    ld.departure_id = "D00"
    led = Ledger()
    notes = inject_delay(ld, 60, {"OX": o}, led, event_time=500, record_time=505)
    assert notes == [] and live_notifications(led) == []
