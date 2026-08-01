"""Travel times: haversine distance x 1.3 road-circuity factor / average speed.
The standard first approximation; swapping in OSRM road times is a one-module change."""
import math

ROAD_FACTOR = 1.3
SPEED_KMH = 60.0

def haversine_km(a, b):
    lat1, lon1, lat2, lon2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    dlat, dlon = lat2 - lat1, lon2 - lon1
    h = math.sin(dlat/2)**2 + math.cos(lat1)*math.cos(lat2)*math.sin(dlon/2)**2
    return 2 * 6371.0 * math.asin(math.sqrt(h))

def road_km(a, b, factor=ROAD_FACTOR):
    return haversine_km(a, b) * factor

def drive_min(a, b, factor=ROAD_FACTOR):
    return road_km(a, b, factor) / SPEED_KMH * 60.0
