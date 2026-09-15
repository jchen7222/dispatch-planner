"""Property tests: conservation, capacity, windows, HANDLING, splits, determinism, quality floor."""
import math
import pytest
from dispatch.generator import make_orders, make_departures, SERVICES
from dispatch.packing import pack
from dispatch.models import Order
from dispatch.run import plan, fingerprint

SEED = 42

@pytest.fixture(scope="module")
def result():
    return plan(seed=SEED, n_orders=80)

def test_conservation(result):
    """orders_in == orders_planned + orders_excepted — nothing lost silently."""
    orders, departures, loads, exceptions, led = result
    planned_ids = {a.order_id for l in loads for a in l.allocations}
    excepted_ids = {e.order_id for e in exceptions}
    assert planned_ids | excepted_ids >= {o.order_id for o in orders}

def test_allocation_mass_conserved(result):
    """Split allocations must sum back to the original order quantities."""
    orders, _, loads, exceptions, _ = result
    excepted = {e.order_id for e in exceptions}
    got = {}
    for l in loads:
        for a in l.allocations:
            got[a.order_id] = got.get(a.order_id, 0) + a.weight_kg
    for o in orders:
        if o.order_id in got and o.order_id not in excepted:
            assert got[o.order_id] == pytest.approx(o.weight_kg, rel=0.01)

def test_capacity_never_exceeded(result):
    _, _, loads, _, _ = result
    for l in loads:
        assert l.weight <= l.model.max_weight_kg + 1e-6
        assert l.cube <= l.model.max_cube_m3 + 1e-6

def test_windows_met(result):
    orders, _, loads, _, _ = result
    omap = {o.order_id: o for o in orders}
    for l in loads:
        for oid, arr in l.stop_arrivals.items():
            o = omap[oid]
            assert o.window_start <= arr <= o.window_end, (l.load_id, oid)

def test_hos_and_shifts(result):
    _, departures, loads, _, _ = result
    dmap = {d.departure_id: d for d in departures}
    per = {}
    for l in loads:
        if not l.departure_id:
            continue
        d = dmap[l.departure_id]
        assert d.accept_from <= l.depart_min and l.arrive_back_min <= d.cutoff
        per.setdefault(l.departure_id, []).append(l)
    for did, ls in per.items():
        assert sum(l.drive_min for l in ls) <= dmap[did].max_handling_min
        ls = sorted(ls, key=lambda l: l.depart_min)
        for a, b in zip(ls, ls[1:]):
            assert a.arrive_back_min <= b.depart_min, f"overlap for {did}"

def test_split_delivery_and_indivisible_exception():
    big_div = Order("BIG1", "NORTH", 43.5, -76.1, 22000, 70.0, 300, 900, True)
    big_ind = Order("BIG2", "NORTH", 43.5, -76.1, 22000, 70.0, 300, 900, False)
    loads, exc = pack([big_div, big_ind], SERVICES)
    alloc_loads = [l for l in loads if any(a.order_id == "BIG1" for a in l.allocations)]
    assert len(alloc_loads) >= 2, "divisible oversize must split across consignments"
    assert sum(a.weight_kg for l in loads for a in l.allocations
               if a.order_id == "BIG1") == pytest.approx(22000, rel=0.01)
    assert any(e.order_id == "BIG2" and e.reason == "indivisible_oversize" for e in exc)

def test_determinism_same_seed_same_plan():
    _, _, l1, _, _ = plan(seed=SEED, n_orders=80)
    _, _, l2, _, _ = plan(seed=SEED, n_orders=80)
    assert fingerprint(l1) == fingerprint(l2)

def test_utilization_floor(result):
    """Quality regression test on the fixed seed: consolidation must actually consolidate."""
    orders, _, loads, _, _ = result
    from dispatch.metrics import naive_shipment_count
    assert len(loads) <= 0.6 * naive_shipment_count(orders, SERVICES)
    fills = [max(l.weight_fill, l.cube_fill) for l in loads if len(l.allocations) > 1]
    assert fills and sum(fills) / len(fills) >= 0.5
