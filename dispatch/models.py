"""Core domain objects. All data synthetic — see README."""
from dataclasses import dataclass, field

@dataclass(frozen=True)
class CarrierService:
    """One shipping company's service level on a corridor. `approved_commodity_classes`
    is a fact about the CARRIER — what it is certified to accept. The business rules in
    config/network.yml may be stricter; eligibility is the intersection of the two."""
    name: str
    max_weight_kg: float
    max_cube_m3: float
    cost_per_km: float
    slots_per_day: int
    approved_commodity_classes: tuple = ("general",)

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
    # general | cosmetics | flammable_liquid | lithium_battery | prohibited
    # NOT "sensitive" — see README. Perfume and nail polish are dangerous goods
    # (flammable liquid) under IATA; lithium cells are DG under UN3480/3481.
    commodity_class: str = "general"

@dataclass(frozen=True)
class Departure:
    """A carrier's scheduled uplift. Tender is accepted from `accept_from` until
    `cutoff`; miss the cutoff and the consignment rolls to the next departure."""
    departure_id: str
    accept_from: int
    cutoff: int
    max_handling_min: int = 660   # total handling minutes this departure can absorb
    max_window_min: int = 840     # length of the acceptance window

@dataclass
class Allocation:
    """Part (or all) of an order placed on one load. Split-delivery = >1 allocation."""
    order_id: str
    weight_kg: float
    cube_m3: float

@dataclass
class Load:
    load_id: str
    model: CarrierService
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
    departure_id: str = ""

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
