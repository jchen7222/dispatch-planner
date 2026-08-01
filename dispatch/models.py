"""Core domain objects. All data synthetic — see README."""
from dataclasses import dataclass, field

@dataclass(frozen=True)
class TruckModel:
    name: str
    max_weight_kg: float
    max_cube_m3: float
    cost_per_km: float
    count_available: int

@dataclass(frozen=True)
class Order:
    order_id: str
    zone: str            # route corridor
    lat: float
    lon: float
    weight_kg: float
    cube_m3: float
    window_start: int    # minutes from midnight
    window_end: int
    divisible: bool
    supplier_id: str = ""
    customer_id: str = ""
    amount: int = 0          # units shipped
    value_usd: float = 0.0   # order value
    service_min: int = 15

@dataclass(frozen=True)
class Driver:
    driver_id: str
    shift_start: int
    shift_end: int
    max_drive_min: int = 660   # simplified FMCSA: 11h driving
    max_duty_min: int = 840    # inside a 14h on-duty window

@dataclass
class Allocation:
    """Part (or all) of an order placed on one load. Split-delivery = >1 allocation."""
    order_id: str
    weight_kg: float
    cube_m3: float

@dataclass
class Load:
    load_id: str
    model: TruckModel
    zone: str
    allocations: list = field(default_factory=list)
    window_start: int = 0        # intersection of member windows
    window_end: int = 24 * 60
    # filled by routing:
    stop_sequence: list = field(default_factory=list)   # order_ids in visit order
    depart_min: int = 0
    arrive_back_min: int = 0
    drive_min: int = 0
    distance_km: float = 0.0
    stop_arrivals: dict = field(default_factory=dict)   # order_id -> arrival minute
    driver_id: str = ""

    @property
    def weight(self):
        return sum(a.weight_kg for a in self.allocations)

    @property
    def cube(self):
        return sum(a.cube_m3 for a in self.allocations)

    @property
    def weight_fill(self):
        return self.weight / self.model.max_weight_kg

    @property
    def cube_fill(self):
        return self.cube / self.model.max_cube_m3

    @property
    def binding_dim(self):
        return "weight" if self.weight_fill >= self.cube_fill else "cube"

@dataclass
class Exception_:
    order_id: str
    reason: str
