"""Seeded synthetic order / carrier / departure generator. SYNTHETIC DATA — see README.

Carrier services, corridors, handling limits and the routing rules are all
TEMPLATE-DEFINED in config/network.yml — configuration-over-code: onboarding a
carrier is one YAML entry and zero Python changes (tests/test_config.py)."""
import os
import random

import yaml

from .eligibility import RuleSet
from .models import Order, CarrierService, Departure

_CFG_PATH = os.path.join(os.path.dirname(__file__), "..", "config", "network.yml")


def load_config(path=None):
    with open(path or _CFG_PATH) as f:
        return yaml.safe_load(f)


def make_services(cfg):
    return [CarrierService(m["name"], float(m["max_weight_kg"]), float(m["max_cube_m3"]),
                           float(m["cost_per_km"]), int(m["count"]),
                           tuple(m.get("approved", ("general",))))
            for m in cfg["services"]]


_CFG = load_config()
DEPOT = (_CFG["depot"]["lat"], _CFG["depot"]["lon"])
ZONES = {z: (v["lat"], v["lon"]) for z, v in _CFG["zones"].items()}
SERVICES = make_services(_CFG)
RULES = RuleSet.from_config(_CFG)   # the versioned routing rule set
HANDLING = _CFG.get("handling", {"max_handling_min": 660, "max_window_min": 840})
DEPARTURES = _CFG.get("departures_cfg", {"starts": [240, 300, 360, 420, 480],
                             "length_min": 840, "departures": 20})

SUPPLIERS = {f"S{i:02d}": {"name": f"Supplier {i:02d}", "notify_channel": "email"}
             for i in range(8)}
CUSTOMERS = {f"C{i:02d}": {"name": f"Customer {i:02d}", "notify_channel": "email"}
             for i in range(12)}


def make_orders(n=80, seed=42):
    rng = random.Random(seed)
    # a SEPARATE stream for commodity class, so adding it did not perturb the
    # weight / window / divisibility draws that existing fixtures depend on
    crng = random.Random(seed + 7)
    orders = []
    for i in range(n):
        zone = rng.choice(list(ZONES))
        zlat, zlon = ZONES[zone]
        heavy = rng.random() < 0.15
        if heavy:
            weight = rng.uniform(5000, 22000)
            cube = weight / rng.uniform(180, 350)   # dense freight
        else:
            weight = rng.uniform(60, 3500)
            cube = weight / rng.uniform(90, 300)
        ws = rng.randrange(300, 900, 15)            # 05:00–15:00 starts
        orders.append(Order(
            order_id=f"O{i:04d}", zone=zone,
            supplier_id=rng.choice(list(SUPPLIERS)),
            customer_id=rng.choice(list(CUSTOMERS)),
            amount=rng.randrange(1, 500),
            value_usd=round(weight * rng.uniform(2.0, 8.0), 2),
            lat=zlat + rng.uniform(-0.15, 0.15),
            lon=zlon + rng.uniform(-0.15, 0.15),
            weight_kg=round(weight, 1), cube_m3=round(max(cube, 0.05), 2),
            window_start=ws, window_end=ws + rng.randrange(150, 420, 30),
            divisible=(weight > 5000 and rng.random() < 0.8) or rng.random() < 0.2,
            # most parcels are general goods; the rest are the restricted classes
            # that decide which carrier may take them at all
            commodity_class=crng.choices(
                ["general", "cosmetics", "flammable_liquid", "lithium_battery", "prohibited"],
                weights=[70, 18, 6, 5, 1])[0],
        ))
    return orders

def make_departures(n=None, seed=42, cfg=None):
    c = cfg or DEPARTURES
    rng = random.Random(seed + 1)
    departures = []
    for i in range(n or c["departures"]):
        start = rng.choice(c["starts"])
        departures.append(Departure(f"D{i:02d}", start, start + c["length_min"],
                              max_handling_min=HANDLING["max_handling_min"],
                              max_window_min=HANDLING["max_window_min"]))
    return departures
