"""Stop sequencing per load: single-vehicle VRPTW via OR-Tools RoutingModel.

Solved twice per load with identical constraints:
  pass 1 (earliest): minimize departure, then arrival-back -> earliest feasible
          departure and the early-departure arrival pattern (may include waiting);
  pass 2 (latest):   maximize departure (just-in-time) -> the latest feasible
          departure, whose span approaches the waitless minimum.
The pair [earliest, latest] becomes the departure-time decision space for the
CP-SAT departure-assignment stage.

Deterministic: PATH_CHEAPEST_ARC first solution, fixed parameters, no
metaheuristic on the default path (a time-boxed GUIDED_LOCAL_SEARCH flag exists
for quality runs; time-based stops are machine-dependent, so CI stays exact)."""
from ortools.constraint_solver import pywrapcp, routing_enums_pb2
from . import geo
from .generator import DEPOT
from .models import Exception_

HORIZON = 24 * 60
EARLIEST_DEPART = 240        # 04:00 planning cutoff


def _solve(stops, locs, maximize_start, use_gls=False, gls_seconds=2, circuity=None, matrix=None, matrix_dist=None):
    n = len(locs)
    manager = pywrapcp.RoutingIndexManager(n, 1, 0)
    routing = pywrapcp.RoutingModel(manager)

    def transit(i, j):
        a, b = manager.IndexToNode(i), manager.IndexToNode(j)
        if matrix is not None:
            t = int(round(matrix[a][b]))
        else:
            t = int(round(geo.drive_min(locs[a], locs[b], circuity or geo.ROAD_FACTOR)))
        if a != 0:
            t += stops[a - 1].service_min
        return t

    cb = routing.RegisterTransitCallback(transit)
    routing.SetArcCostEvaluatorOfAllVehicles(cb)
    routing.AddDimension(cb, HORIZON, HORIZON, False, "Time")
    tdim = routing.GetDimensionOrDie("Time")
    for idx, o in enumerate(stops, start=1):
        tdim.CumulVar(manager.NodeToIndex(idx)).SetRange(o.window_start, o.window_end)
    tdim.CumulVar(routing.Start(0)).SetRange(EARLIEST_DEPART, HORIZON)

    if maximize_start:
        routing.AddVariableMaximizedByFinalizer(tdim.CumulVar(routing.Start(0)))
        routing.AddVariableMinimizedByFinalizer(tdim.CumulVar(routing.End(0)))
    else:
        routing.AddVariableMinimizedByFinalizer(tdim.CumulVar(routing.Start(0)))
        routing.AddVariableMinimizedByFinalizer(tdim.CumulVar(routing.End(0)))

    params = pywrapcp.DefaultRoutingSearchParameters()
    params.first_solution_strategy = routing_enums_pb2.FirstSolutionStrategy.PATH_CHEAPEST_ARC
    if use_gls:
        params.local_search_metaheuristic = routing_enums_pb2.LocalSearchMetaheuristic.GUIDED_LOCAL_SEARCH
        params.time_limit.FromSeconds(gls_seconds)
    sol = routing.SolveWithParameters(params)
    if sol is None:
        return None
    seq, arrivals = [], {}
    index = routing.Start(0)
    depart = sol.Value(tdim.CumulVar(index))
    dist, prev = 0.0, 0
    while not routing.IsEnd(index):
        nxt = sol.Value(routing.NextVar(index))
        node = manager.IndexToNode(nxt) if not routing.IsEnd(nxt) else 0
        dist += (matrix_dist[prev][node] if matrix_dist is not None
                 else geo.road_km(locs[prev], locs[node]))
        if not routing.IsEnd(nxt):
            o = stops[node - 1]
            seq.append(o.order_id)
            arrivals[o.order_id] = sol.Value(tdim.CumulVar(nxt))
        prev = node
        index = nxt
    end_time = sol.Value(tdim.CumulVar(routing.End(0)))
    return depart, end_time, seq, arrivals, dist


def route_load(load, orders_by_id, use_gls=False, gls_seconds=2, calibration=None, provider=None):
    """Sequence one load's stops. Mutates load in place. Returns None on success,
    or an Exception_ if no window-feasible sequence exists."""
    stops, seen = [], set()
    for a in load.allocations:
        if a.order_id not in seen:
            seen.add(a.order_id)
            stops.append(orders_by_id[a.order_id])
    locs = [DEPOT] + [(o.lat, o.lon) for o in stops]

    circ = None
    if calibration:
        z = calibration.get("corridors", {}).get(load.zone) or {}
        circ = z.get("circuity_fit")
    dur_matrix = dist_matrix = None
    if provider is not None:
        dur_matrix, dist_matrix = provider.matrix(locs)
    early = _solve(stops, locs, maximize_start=False, use_gls=use_gls,
                   gls_seconds=gls_seconds, circuity=circ,
                   matrix=dur_matrix, matrix_dist=dist_matrix)
    if early is None:
        return Exception_(load.load_id, "window_infeasible")
    depart, end_time, seq, arrivals, dist = early

    late = _solve(stops, locs, maximize_start=True, circuity=circ, matrix=dur_matrix, matrix_dist=dist_matrix)
    latest_depart, latest_end = (late[0], late[1]) if late else (depart, end_time)

    load.stop_sequence = seq
    load.stop_arrivals = arrivals
    load.depart_min = depart
    load.arrive_back_min = end_time
    load.latest_depart = max(depart, latest_depart)
    load.distance_km = round(dist, 1)

    service_total = sum(s.service_min for s in stops)
    pure_drive = 0.0
    if dur_matrix is not None:
        idx = {(o.lat, o.lon): k + 1 for k, o in enumerate(stops)}
        path = [0] + [idx[(orders_by_id[oid].lat, orders_by_id[oid].lon)] for oid in seq] + [0]
        for a, b in zip(path, path[1:]):
            pure_drive += dur_matrix[a][b]
    else:
        seq_pts = [DEPOT] + [(orders_by_id[oid].lat, orders_by_id[oid].lon) for oid in seq] + [DEPOT]
        for a, b in zip(seq_pts, seq_pts[1:]):
            pure_drive += geo.drive_min(a, b)
    load.drive_min = int(round(pure_drive))
    # waitless span: what the route takes when departing just-in-time
    load.span_min = int(min(max(latest_end - latest_depart, pure_drive + service_total),
                            end_time - depart)) if late else int(end_time - depart)
    return None
