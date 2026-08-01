"""The add-a-truck-model proof: a fleet instantiates from a fixture template
with an extra model — one YAML entry, zero Python changes."""
import os

from dispatch.generator import load_config, make_fleet, make_orders
from dispatch.packing import pack

ALT = os.path.join(os.path.dirname(__file__), "fixtures", "fleet_alt.yml")


def test_fleet_instantiates_from_template():
    cfg = load_config(ALT)
    fleet = make_fleet(cfg)
    names = [m.name for m in fleet]
    assert "CargoBike" in names, "the new model exists purely as config"
    bike = next(m for m in fleet if m.name == "CargoBike")
    assert (bike.max_weight_kg, bike.count_available) == (150, 6)


def test_new_model_participates_in_packing_without_code_changes():
    cfg = load_config(ALT)
    fleet = make_fleet(cfg)
    orders = [o for o in make_orders(40, seed=3) if o.weight_kg <= 150][:6]
    assert orders, "need some bike-sized orders"
    loads, exc = pack(orders, fleet)
    assert any(l.model.name == "CargoBike" for l in loads), \
        "small orders pack onto the template-defined model"
