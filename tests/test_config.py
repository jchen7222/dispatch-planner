"""The onboard-a-carrier proof: a network instantiates from a fixture template
carrying an extra service and its own rule set — one YAML file, zero Python
changes. `MiniParcel` and rule `A4` appear in no source file."""
import os

from dispatch.eligibility import RuleSet
from dispatch.generator import load_config, make_services, make_orders
from dispatch.packing import pack

ALT = os.path.join(os.path.dirname(__file__), "fixtures", "network_alt.yml")


def _decisions(orders, services, cfg):
    rules = RuleSet.from_config(cfg)
    return {o.order_id: rules.decide(o, services) for o in orders}


def test_carrier_instantiates_from_template():
    cfg = load_config(ALT)
    services = make_services(cfg)
    names = [m.name for m in services]
    assert "MiniParcel" in names, "the new service exists purely as config"
    mini = next(m for m in services if m.name == "MiniParcel")
    assert (mini.max_weight_kg, mini.slots_per_day) == (150, 6)
    assert "cosmetics" in mini.approved_commodity_classes


def test_new_carrier_participates_in_packing_without_code_changes():
    cfg = load_config(ALT)
    services = make_services(cfg)
    orders = [o for o in make_orders(200, seed=3)
              if o.weight_kg <= 150 and o.commodity_class in ("general", "cosmetics")][:6]
    assert orders, "need some parcel-sized orders"
    loads, exc = pack(orders, services, decisions=_decisions(orders, services, cfg))
    assert any(l.model.name == "MiniParcel" for l in loads), \
        "small orders pack onto the template-defined service"


def test_rule_set_version_comes_from_the_template():
    cfg = load_config(ALT)
    rules = RuleSet.from_config(cfg)
    assert rules.version == "alt-v1"
    assert [r["id"] for r in rules.rules] == ["A1", "A2", "A3", "A4", "A5"]
