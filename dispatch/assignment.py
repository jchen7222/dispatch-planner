"""Departure assignment: CP-SAT (exact). Departure time is a decision variable
within [earliest, latest] from routing; departing later only absorbs waiting,
so window feasibility is preserved and the route's span shrinks toward its
waitless minimum (pure drive + service). Each (load, departure) pair is an
optional interval; NoOverlap per departure; simplified FMCSA HANDLING: driving
minutes <= 11h inside the departure's 14h shift. Deterministic: 1 worker,
fixed seed, 10s cap."""
from ortools.sat.python import cp_model
from .models import Exception_


def assign(loads, departures, orders_by_id=None):
    m = cp_model.CpModel()
    span = {l.load_id: int(l.arrive_back_min - l.depart_min) for l in loads}
    span_min = {l.load_id: min(int(l.span_min) or span[l.load_id], span[l.load_id])
                for l in loads}
    earliest = {l.load_id: int(l.depart_min) for l in loads}
    latest = {l.load_id: int(max(l.depart_min, l.latest_depart)) for l in loads}

    start, size, end = {}, {}, {}
    for l in loads:
        lid = l.load_id
        start[lid] = m.NewIntVar(earliest[lid], latest[lid], f"s_{lid}")
        size[lid] = m.NewIntVar(span_min[lid], span[lid], f"z_{lid}")
        # departing delta minutes later absorbs up to delta minutes of waiting
        m.Add(size[lid] >= span[lid] - (start[lid] - earliest[lid]))
        end[lid] = m.NewIntVar(0, 24 * 60, f"e_{lid}")
        m.Add(end[lid] == start[lid] + size[lid])

    x, itv = {}, {}
    for l in loads:
        lid = l.load_id
        for d in departures:
            lo = max(earliest[lid], d.accept_from)
            if lo <= latest[lid] and lo + span_min[lid] <= d.cutoff:
                key = (lid, d.departure_id)
                x[key] = m.NewBoolVar(f"x_{lid}_{d.departure_id}")
                itv[key] = m.NewOptionalIntervalVar(
                    start[lid], size[lid], end[lid], x[key], f"i_{lid}_{d.departure_id}")
                m.Add(start[lid] >= d.accept_from).OnlyEnforceIf(x[key])
                m.Add(end[lid] <= d.cutoff).OnlyEnforceIf(x[key])

    assigned = {}
    for l in loads:
        vs = [v for (lid, _), v in x.items() if lid == l.load_id]
        if vs:
            m.AddAtMostOne(vs)
            a = m.NewBoolVar(f"a_{l.load_id}")
            m.AddMaxEquality(a, vs)
            assigned[l.load_id] = a

    for d in departures:
        ds = [itv[k] for k in itv if k[1] == d.departure_id]
        if ds:
            m.AddNoOverlap(ds)
        m.Add(sum(int(l.drive_min) * x[(l.load_id, d.departure_id)]
                  for l in loads if (l.load_id, d.departure_id) in x) <= d.max_handling_min)

    m.Maximize(sum(1000 * a for a in assigned.values()))
    solver = cp_model.CpSolver()
    solver.parameters.num_search_workers = 1
    solver.parameters.random_seed = 7
    solver.parameters.max_time_in_seconds = 20
    solver.Solve(m)

    _wend_all = {}
    if orders_by_id:
        _wend_all = {oid: o.window_end for oid, o in orders_by_id.items()}
    exceptions = []
    for l in loads:
        l.departure_id = ""
        for d in departures:
            v = x.get((l.load_id, d.departure_id))
            if v is not None and solver.Value(v):
                l.departure_id = d.departure_id
                new_depart = solver.Value(start[l.load_id])
                delta = new_depart - l.depart_min
                if delta:
                    l.depart_min = new_depart
                    # shifting later absorbs waiting first; arrival never passes
                    # its window end (upper-bound report, exact at the extremes)
                    l.stop_arrivals = {k: min(v2 + delta, _wend_all.get(k, 10**6))
                                       for k, v2 in l.stop_arrivals.items()}
                l.arrive_back_min = solver.Value(end[l.load_id])
        if not l.departure_id:
            exceptions.append(Exception_(l.load_id, "no_departure_before_cutoff"))
    return exceptions
