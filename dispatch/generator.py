"""Seeded synthetic order/fleet/driver generator. SYNTHETIC DATA — labeled per README.

The fleet, depot, corridors, and HOS parameters are TEMPLATE-DEFINED in
config/fleet.yml — configuration-over-code: a new truck model is one YAML
entry and zero Python changes (proved by tests/test_config.py)."""
import os
import random

import yaml

from .models import Order, TruckModel, Driver

_CFG_PATH = os.path.join(os.path.dirname(__file__), "..", "config", "fleet.yml")


def load_config(path=None):
    with open(path or _CFG_PATH) as f:
        return yaml.safe_load(f)


def make_fleet(cfg):
    return [TruckModel(m["name"], float(m["max_weight_kg"]), float(m["max_cube_m3"]),
                       float(m["cost_per_km"]), int(m["count"]))
            for m in cfg["fleet"]]


_CFG = load_config()
DEPOT = (_CFG["depot"]["lat"], _CFG["depot"]["lon"])
ZONES = {z: (v["lat"], v["lon"]) for z, v in _CFG["zones"].items()}
FLEET = make_fleet(_CFG)
HOS = _CFG.get("hos", {"max_drive_min": 660, "max_duty_min": 840})
SHIFTS = _CFG.get("shifts", {"starts": [240, 300, 360, 420, 480],
                             "length_min": 840, "drivers": 20})

SUPPLIERS = {f"S{i:02d}": {"name": f"Supplier {i:02d}", "notify_channel": "email"}
             for i in range(8)}
CUSTOMERS = {f"C{i:02d}": {"name": f"Customer {i:02d}", "notify_channel": "email"}
             for i in range(12)}

DEPOT = (43.05, -76.15)  # single depot

ZONES = {                # six corridors: (center_lat, center_lon)
    "NORTH": (43.55, -76.10), "SOUTH": (42.60, -76.20),
    "EAST":  (43.05, -75.20), "WEST":  (43.00, -77.10),
    "NE":    (43.45, -75.40), "SW":    (42.70, -76.90),
}

FLEET = [
    TruckModel("Sprinter", 1500,  12.0, 0.50, 18),
    TruckModel("BoxTruck", 4500,  28.0, 0.80, 18),
    TruckModel("Semi",    15000,  75.0, 1.40, 14),
]

def make_orders(n=80, seed=42):
    rng = random.Random(seed)
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
        ))
    return orders

def make_drivers(n=None, seed=42, cfg=None):
    c = cfg or SHIFTS
    rng = random.Random(seed + 1)
    drivers = []
    for i in range(n or c["drivers"]):
        start = rng.choice(c["starts"])
        drivers.append(Driver(f"D{i:02d}", start, start + c["length_min"],
                              max_drive_min=HOS["max_drive_min"],
                              max_duty_min=HOS["max_duty_min"]))
    return drivers
