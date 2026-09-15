"""Carrier eligibility and the auditability of a routing decision.

The property that matters: a decision is reproducible after the rules change.
Edit the rule set, and the parcel that shipped last month must still be
explainable under the rules that were in force when it shipped.
"""
from dispatch.eligibility import RuleSet, decide_all
from dispatch.generator import RULES, SERVICES, load_config, make_services
from dispatch.ledger import Ledger
from dispatch.models import Order
from dispatch.packing import pack


def _order(oid, commodity_class, weight=10.0, zone="NORTH"):
    return Order(order_id=oid, zone=zone, lat=43.5, lon=-76.1,
                 weight_kg=weight, cube_m3=0.1,
                 window_start=480, window_end=900, divisible=False,
                 commodity_class=commodity_class)


# ── eligibility is capability ∩ rule ─────────────────────────────────────────

def test_general_goods_may_use_the_economy_service():
    d = RULES.decide(_order("O1", "general", weight=10), SERVICES)
    assert d.ok and "Economy" in d.eligible_services
    assert d.matched_rule == "R6"


def test_flammable_liquid_is_confined_to_the_dg_service():
    d = RULES.decide(_order("O2", "flammable_liquid", weight=2), SERVICES)
    assert d.eligible_services == ("DGExpress",), \
        "perfume is IATA flammable liquid — it may not ride a general service"
    assert d.matched_rule == "R3"


def test_lithium_battery_is_confined_to_the_dg_service():
    d = RULES.decide(_order("O3", "lithium_battery", weight=1), SERVICES)
    assert d.eligible_services == ("DGExpress",)


def test_weight_band_narrows_the_services_for_cosmetics():
    small = RULES.decide(_order("O4", "cosmetics", weight=5), SERVICES)
    bulk = RULES.decide(_order("O5", "cosmetics", weight=80), SERVICES)
    assert set(small.eligible_services) == {"Standard", "DGExpress"}
    assert bulk.eligible_services == ("DGExpress",)
    assert (small.matched_rule, bulk.matched_rule) == ("R4", "R5")


def test_prohibited_goods_have_no_lane_at_all():
    d = RULES.decide(_order("O6", "prohibited"), SERVICES)
    assert not d.ok and d.reason == "prohibited" and d.matched_rule == "R1"


def test_an_unknown_class_is_refused_by_name_not_by_silence():
    d = RULES.decide(_order("O7", "radioactive"), SERVICES)
    assert not d.ok
    assert d.matched_rule == "NO_RULE" and d.reason == "no_rule_for_commodity_class"


def test_carrier_capability_can_be_stricter_than_the_rule():
    """A rule may allow a service the carrier is not certified for; the
    intersection wins, so the uncertified service never appears."""
    permissive = RuleSet("vX", [{"id": "X1",
                                 "when": {"commodity_class": "flammable_liquid"},
                                 "allow": ["Economy", "Standard", "DGExpress"]}])
    d = permissive.decide(_order("O8", "flammable_liquid"), SERVICES)
    assert d.eligible_services == ("DGExpress",), \
        "Economy and Standard are not approved for flammable liquid"


# ── the packer obeys the decision ────────────────────────────────────────────

def test_restricted_parcels_never_pack_onto_an_unapproved_service():
    orders = [_order(f"O{i:02d}", "flammable_liquid", weight=3) for i in range(6)]
    decisions = {o.order_id: RULES.decide(o, SERVICES) for o in orders}
    loads, exc = pack(orders, SERVICES, decisions=decisions)
    assert loads, "they should ship — just only on the DG service"
    assert {l.model.name for l in loads} == {"DGExpress"}


def test_a_refused_parcel_is_an_exception_naming_the_rule_not_a_silent_drop():
    orders = [_order("OK1", "general", weight=10), _order("BAD", "prohibited")]
    decisions = {o.order_id: RULES.decide(o, SERVICES) for o in orders}
    loads, exc = pack(orders, SERVICES, decisions=decisions)
    packed = {a.order_id for l in loads for a in l.allocations}
    assert "BAD" not in packed and "OK1" in packed
    reason = next(e.reason for e in exc if e.order_id == "BAD")
    assert reason == "prohibited:v1:R1", \
        "the exception carries the rule-set version and the rule that refused it"


# ── the decision is auditable after the rules change ─────────────────────────

def test_the_decision_event_carries_the_rule_version_that_produced_it():
    led = Ledger()
    orders = [_order("O1", "cosmetics", weight=5)]
    decide_all(orders, SERVICES, RULES, ledger=led, record_time=210)
    ev = led.history("O1")[0]
    assert ev["type"] == "routing_decided"
    assert ev["payload"]["rule_set_version"] == "v1"
    assert ev["payload"]["matched_rule"] == "R4"
    assert ev["payload"]["eligible_services"] == ["Standard", "DGExpress"]


def test_a_past_decision_stays_explainable_after_the_rules_change():
    """This is the whole point. v1 sent small cosmetics to Standard. v2 no longer
    does. The shipment that went out under v1 is still traceable to v1/R4 — you
    do not have to reconstruct what the rules used to say."""
    led = Ledger()
    order = _order("O1", "cosmetics", weight=5)
    decide_all([order], SERVICES, RULES, ledger=led, record_time=210)

    v2 = RuleSet("v2", [{"id": "R4", "when": {"commodity_class": "cosmetics"},
                         "allow": ["DGExpress"]}])          # policy tightened
    assert v2.decide(order, SERVICES).eligible_services == ("DGExpress",)

    stamped = led.history("O1")[0]["payload"]
    assert stamped["rule_set_version"] == "v1"
    assert "Standard" in stamped["eligible_services"], \
        "the shipped decision is unchanged by a later rule edit"


def test_blast_radius_is_a_query_over_the_rule_that_fired():
    """Find every parcel routed by one rule under one version — the recall list."""
    led = Ledger()
    orders = ([_order(f"F{i}", "flammable_liquid", weight=2) for i in range(3)]
              + [_order(f"G{i}", "general", weight=10) for i in range(4)])
    decide_all(orders, SERVICES, RULES, ledger=led, record_time=210)
    hit = [e["entity_id"] for e in led.events
           if e["type"] == "routing_decided"
           and e["payload"]["rule_set_version"] == "v1"
           and e["payload"]["matched_rule"] == "R3"]
    assert sorted(hit) == ["F0", "F1", "F2"]
