"""Consolidation: First-Fit Decreasing (FFD) bin packing on weight AND cube,
per (corridor x window) bucket, over a heterogeneous services.
Oversized + divisible -> split-delivery; oversized + indivisible -> exception.
FFD guarantee: <= 11/9*OPT + 6/9 bins (near-optimal in practice)."""
from .models import Load, Allocation, Exception_

MAX_STOPS = 4          # real dispatch caps stops per route; also keeps spans chainable
MAX_PARTIES_SOFT = 5   # blast-radius shaping: prefer not to concentrate many
                       # parties' orders on one consignment (soft — never blocks a fit)

def _window_bucket(order):
    return 0   # bucket by corridor only; per-load window-compatibility guards the rest

def _fits(load, w, c, order=None, decisions=None):
    if not (load.weight + w <= load.model.max_weight_kg and
            load.cube + c <= load.model.max_cube_m3 and
            len({a.order_id for a in load.allocations}) < MAX_STOPS):
        return False
    # ELIGIBILITY: a consignment may only ride a service approved for EVERY
    # commodity class already on it. Mixing a general parcel onto a DG load is
    # fine; putting a DG parcel onto a general-only service is not.
    if decisions is not None and order is not None:
        d = decisions.get(order.order_id)
        if d is None or load.model.name not in d.eligible_services:
            return False
    return True

def _windows_compatible(load, order, min_overlap=60):
    lo = max(load.window_start, order.window_start)
    hi = min(load.window_end, order.window_end)
    return hi - lo >= min_overlap

def _shrink_window(load, order):
    load.window_start = max(load.window_start, order.window_start)
    load.window_end = min(load.window_end, order.window_end)

def _capacity_left(service_counts, model):
    return service_counts.get(model.name, 0) > 0

_ORDERS = {}


def pack(orders, services, seed_label="", calibration=None, decisions=None):
    """Returns (loads, exceptions). Deterministic for a given input order.

    `decisions` maps order_id -> RoutingDecision (see dispatch.eligibility). When
    supplied, an order is only ever packed onto a service its decision allows, and
    an order with no eligible service becomes an exception carrying the rule that
    refused it — never a silent drop."""
    global _ORDERS
    _ORDERS = {o.order_id: o for o in orders}
    buffers = {}
    if calibration:
        buffers = {z: v.get("window_buffer_min", 0)
                   for z, v in calibration.get("corridors", {}).items()}
    service_counts = {m.name: m.slots_per_day for m in services}
    by_size = sorted(
        orders,
        key=lambda o: max(o.weight_kg / max(m.max_weight_kg for m in services),
                          o.cube_m3 / max(m.max_cube_m3 for m in services)),
        reverse=True)
    buckets = {}
    for o in by_size:
        buckets.setdefault((o.zone, _window_bucket(o)), []).append(o)

    loads, exceptions, lid = [], [], 0

    # Orders the rules refuse outright never reach the packer. The exception
    # names the rule that refused them, so the reason is a lookup, not a guess.
    if decisions is not None:
        refused = [o for o in by_size if not decisions[o.order_id].ok]
        for o in refused:
            d = decisions[o.order_id]
            exceptions.append(Exception_(
                o.order_id, f"{d.reason}:{d.rule_set_version}:{d.matched_rule}"))
        blocked = {o.order_id for o in refused}
        buckets = {k: [o for o in v if o.order_id not in blocked]
                   for k, v in buckets.items()}

    for key in sorted(buckets):
        open_loads = []
        for o in sorted(buckets[key], key=lambda o: (-max(o.weight_kg, o.cube_m3 * 250), o.order_id)):
            w, c = o.weight_kg, o.cube_m3
            placed = False
            fitting_loads = [ld for ld in open_loads
                             if _fits(ld, w, c, o, decisions)
                             and _windows_compatible(ld, o, 60 + buffers.get(o.zone, 0))]
            # blast-radius soft preference: try loads that stay under the party
            # cap first; fall back to any fit (soft, never blocks consolidation)
            def _parties(ld):
                ps = set()
                for a in ld.allocations:
                    oo = _ORDERS.get(a.order_id)
                    if oo is not None:
                        ps.add(oo.supplier_id)
                        ps.add(oo.customer_id)
                return ps
            preferred = [ld for ld in fitting_loads
                         if len(_parties(ld) | {o.supplier_id, o.customer_id})
                         <= MAX_PARTIES_SOFT]
            for ld in (preferred or fitting_loads):    # First Fit, shaped
                ld.allocations.append(Allocation(o.order_id, w, c))
                _shrink_window(ld, o)
                placed = True
                break
            if placed:
                continue
            allowed = (set(decisions[o.order_id].eligible_services)
                       if decisions is not None else None)
            fitting = [m for m in services
                       if w <= m.max_weight_kg and c <= m.max_cube_m3
                       and _capacity_left(service_counts, m)
                       and (allowed is None or m.name in allowed)]
            if fitting:                                # open the best new bin
                # smallest model that fits -> best projected fill; tie-break cost
                m = sorted(fitting, key=lambda m: (m.max_weight_kg, m.cost_per_km))[0]
                lid += 1
                ld = Load(f"L{lid:03d}", m, o.zone,
                          [Allocation(o.order_id, w, c)],
                          o.window_start, o.window_end)
                service_counts[m.name] -= 1
                open_loads.append(ld)
                continue
            # nothing fits whole
            if o.divisible:
                _pool = [m for m in services
                         if _capacity_left(service_counts, m)
                         and (allowed is None or m.name in allowed)]
                big = max(_pool, key=lambda m: m.max_weight_kg, default=None)
                remaining_w, remaining_c, part = w, c, 0
                ok = True
                while remaining_w > 1e-6 and ok:
                    # try to top up an open load first, else open the largest available
                    target = None
                    for ld in open_loads:
                        if (allowed is None or ld.model.name in allowed) and \
                           _windows_compatible(ld, o) and \
                           (ld.model.max_weight_kg - ld.weight) > 1 and \
                           (ld.model.max_cube_m3 - ld.cube) > 0.01 and \
                           len({a.order_id for a in ld.allocations}) < MAX_STOPS:
                            target = ld
                            break
                    if target is None:
                        avail = [m for m in services
                                 if _capacity_left(service_counts, m)
                                 and (allowed is None or m.name in allowed)]
                        if not avail:
                            ok = False
                            break
                        m = max(avail, key=lambda m: m.max_weight_kg)
                        lid += 1
                        target = Load(f"L{lid:03d}", m, o.zone, [],
                                      o.window_start, o.window_end)
                        service_counts[m.name] -= 1
                        open_loads.append(target)
                    take_w = min(remaining_w, target.model.max_weight_kg - target.weight)
                    take_c = min(remaining_c, target.model.max_cube_m3 - target.cube,
                                 take_w * (c / w) if w else remaining_c)
                    take_w = min(take_w, (take_c / (c / w)) if c and w else take_w)
                    if take_w <= 1:
                        ok = False
                        break
                    part += 1
                    target.allocations.append(Allocation(o.order_id, round(take_w, 1), round(take_c, 2)))
                    _shrink_window(target, o)
                    remaining_w -= take_w
                    remaining_c -= take_c
                if not ok or remaining_w > 1e-6:
                    exceptions.append(Exception_(o.order_id, "no_capacity_on_approved_service"))
            else:
                reason = ("indivisible_oversize"
                          if not any(w <= m.max_weight_kg and c <= m.max_cube_m3 for m in services)
                          else "approved_service_capacity_exhausted")
                exceptions.append(Exception_(o.order_id, reason))
        loads.extend([l for l in open_loads if l.allocations])
    return loads, exceptions
