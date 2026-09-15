"""Stage 5A — delay impact and party notification.

A `consignment_delayed` event triggers a compensating `plan_amended` (never an
overwrite), then the impact walk: consignment -> its stops -> the orders on them ->
each order's supplier and customer. Delay is measured against the PROMISE
(the order's window_end), not the plan: a consignment can run late and still
deliver inside the window — that is a delay of zero and no notification.

One `delay_notification_queued` event per affected party per delay, scoped
to that party's own orders only. A second delay on the same consignment emits a
superseding notification (idempotency key: party x consignment), so the ledger
holds the full who-was-told-what history while exactly one notification per
party is live. No email is sent; the queue IS the deliverable."""
import math
from .generator import SUPPLIERS, CUSTOMERS

MIN_PER_DAY = 24 * 60


def _last_live(ledger, entity_id, types):
    superseded = {e["corrects_seq"] for e in ledger.events if e["corrects_seq"] is not None}
    last = None
    for e in ledger.events:
        if e["entity_id"] == entity_id and e["type"] in types and e["seq"] not in superseded:
            last = e
    return last


def inject_delay(load, delay_min, orders_by_id, ledger, event_time, record_time):
    """Apply a delay to one planned load. Returns the list of live notification
    events created (possibly empty, when every arrival stays inside its window)."""
    ledger.append("consignment_delayed", load.load_id,
                  {"delay_min": delay_min, "departure": load.departure_id},
                  event_time=event_time, record_time=record_time)

    new_arrivals = {oid: t + delay_min for oid, t in load.stop_arrivals.items()}
    prior_plan = _last_live(ledger, load.load_id, ("load_plan_created", "plan_amended"))
    ledger.append("plan_amended", load.load_id, {
        "model": load.model.name, "departure": load.departure_id,
        "orders": [a.order_id for a in load.allocations],
        "arrivals": new_arrivals,
        "note": f"compensates consignment_delayed +{delay_min}min",
    }, event_time=event_time, record_time=record_time + 1,
        corrects=prior_plan["seq"] if prior_plan else None)
    load.stop_arrivals = new_arrivals
    load.arrive_back_min += delay_min

    # impact walk: only orders now past their PROMISED window end are late
    late = []
    for oid, eta in new_arrivals.items():
        o = orders_by_id[oid]
        minutes_late = eta - o.window_end
        if minutes_late > 0:
            late.append((o, eta, minutes_late,
                         math.ceil(minutes_late / MIN_PER_DAY)))

    by_party = {}
    for o, eta, minutes_late, days in late:
        for role, party in (("supplier", o.supplier_id), ("customer", o.customer_id)):
            by_party.setdefault(party, []).append({
                "order_id": o.order_id, "role": role, "amount": o.amount,
                "value_usd": o.value_usd, "new_eta_min": eta,
                "minutes_late": minutes_late, "days_of_delay": days,
            })

    notifications = []
    for party, rows in sorted(by_party.items()):
        key = f"{party}|{load.load_id}"
        prior = _last_live(ledger, key, ("delay_notification_queued",))
        name = (SUPPLIERS.get(party) or CUSTOMERS.get(party) or {}).get("name", party)
        payload = {
            "party": party, "party_name": name, "consignment": load.load_id,
            "orders": rows,
            "total_value_usd": round(sum(r["value_usd"] for r in rows), 2),
            "max_days_of_delay": max(r["days_of_delay"] for r in rows),
        }
        seq = ledger.append("delay_notification_queued", key, payload,
                            event_time=event_time, record_time=record_time + 2,
                            corrects=prior["seq"] if prior else None)
        notifications.append(ledger.events[seq])
    return notifications


def render(notification):
    p = notification["payload"]
    lines = [f"NOTIFY {p['party']} ({p['party_name']}) — consignment {p['consignment']} delayed:"]
    for r in p["orders"]:
        lines.append(f"  {r['order_id']} ({r['role']}): {r['amount']} units, "
                     f"${r['value_usd']:,.2f} — {r['days_of_delay']} day(s) late "
                     f"({r['minutes_late']} min past window)")
    lines.append(f"  total value at delay: ${p['total_value_usd']:,.2f}")
    return "\n".join(lines)


def live_notifications(ledger):
    """Exactly the notifications currently in force (superseded ones excluded)."""
    state = ledger.fold_current()
    return [e for e in state.values() if e["type"] == "delay_notification_queued"]
