"""Analytics on DuckDB: utilization, naive-baseline comparison, OTIF under
injected delay, exceptions. (Porting these views to dbt models is
extension exercise #2 — see README.)"""
import duckdb

def naive_truck_count(orders, fleet):
    """Baseline: no consolidation — each order alone on the smallest fitting model."""
    n = 0
    for o in orders:
        if any(o.weight_kg <= m.max_weight_kg and o.cube_m3 <= m.max_cube_m3 for m in fleet):
            n += 1
        else:
            n += 2   # would need a split even alone
    return n

def build(loads, orders, exceptions, fleet, delay_min=90, delay_every=5):
    con = duckdb.connect()
    con.execute("""CREATE TABLE loads(load_id VARCHAR, model VARCHAR, zone VARCHAR,
        weight DOUBLE, cube DOUBLE, weight_fill DOUBLE, cube_fill DOUBLE,
        binding VARCHAR, stops INT, distance_km DOUBLE, drive_min INT,
        depart INT, back INT, driver VARCHAR)""")
    for l in loads:
        con.execute("INSERT INTO loads VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            [l.load_id, l.model.name, l.zone, l.weight, l.cube,
             round(l.weight_fill, 3), round(l.cube_fill, 3), l.binding_dim,
             len(l.stop_sequence), l.distance_km, int(l.drive_min),
             l.depart_min, l.arrive_back_min, l.driver_id])
    con.execute("CREATE TABLE stops(load_id VARCHAR, order_id VARCHAR, arrival INT, wend INT, value_usd DOUBLE)")
    omap = {o.order_id: o for o in orders}
    i = 0
    for l in loads:
        for oid in l.stop_sequence:
            i += 1
            delayed = l.stop_arrivals[oid] + (delay_min if i % delay_every == 0 else 0)
            con.execute("INSERT INTO stops VALUES (?,?,?,?,?)",
                        [l.load_id, oid, delayed, omap[oid].window_end,
                         omap[oid].value_usd])
    con.execute("CREATE TABLE exceptions(entity VARCHAR, reason VARCHAR)")
    for e in exceptions:
        con.execute("INSERT INTO exceptions VALUES (?,?)", [e.order_id, e.reason])

    util = con.execute("""SELECT model, count(*) AS trucks,
        round(avg(CASE WHEN binding='weight' THEN weight_fill ELSE cube_fill END),3) AS avg_binding_fill,
        sum(CASE WHEN binding='weight' THEN 1 ELSE 0 END) AS weigh_out,
        sum(CASE WHEN binding='cube' THEN 1 ELSE 0 END)  AS cube_out
        FROM loads GROUP BY model ORDER BY model""").fetchall()
    otif = con.execute(
        "SELECT round(100.0*sum(CASE WHEN arrival<=wend THEN 1 ELSE 0 END)/count(*),1) FROM stops"
    ).fetchone()[0]
    otif_by_value = con.execute(
        "SELECT round(100.0*sum(CASE WHEN arrival<=wend THEN value_usd ELSE 0 END)/sum(value_usd),1) FROM stops"
    ).fetchone()[0]
    con.execute("CREATE TABLE order_parties(order_id VARCHAR, supplier VARCHAR, customer VARCHAR, value_usd DOUBLE)")
    for o in orders:
        con.execute("INSERT INTO order_parties VALUES (?,?,?,?)",
                    [o.order_id, o.supplier_id, o.customer_id, o.value_usd])
    blast = con.execute("""SELECT s.load_id,
        count(DISTINCT p.supplier) + count(DISTINCT p.customer) AS parties,
        round(sum(p.value_usd),2) AS total_value_usd
        FROM stops s JOIN order_parties p USING(order_id)
        GROUP BY 1 ORDER BY (count(DISTINCT p.supplier) + count(DISTINCT p.customer)) * sum(p.value_usd) DESC LIMIT 5""").fetchall()
    exc = con.execute(
        "SELECT reason, count(*) FROM exceptions GROUP BY reason ORDER BY 2 DESC").fetchall()
    totals = con.execute(
        "SELECT count(*), round(sum(distance_km),1), count(DISTINCT CASE WHEN driver <> '' THEN driver END) FROM loads").fetchone()
    return {"util": util, "otif_pct_with_injected_delays": otif,
            "otif_by_value_pct": otif_by_value, "blast_radius_top5": blast,
            "exceptions": exc, "trucks_used": totals[0],
            "total_km": totals[1], "drivers_used": totals[2],
            "naive_trucks": naive_truck_count(orders, fleet)}
