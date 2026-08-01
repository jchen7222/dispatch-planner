"""Stage 3A — travel times as a pluggable provider (the API-integration layer).

Three implementations behind one interface:
  * HaversineProvider — haversine x circuity / speed; zero dependencies,
    always works; the default and the degrade target.
  * OSRMProvider — real road times from an OSRM /table endpoint
    (self-hosted or the public demo server), keyless.
  * GoogleMatrixProvider — Distance Matrix API; key via GOOGLE_MAPS_API_KEY,
    never committed.

Live providers run through ResilientClient: retry with exponential backoff,
rate limiting, response caching, and a circuit breaker that degrades
gracefully to haversine when the API is down — failure as the normal case.

CI never calls a live API: a record/replay cassette (fixtures/cassettes/)
captures real responses once and replays them deterministically. The shipped
OSRM cassette contains REAL responses recorded from the public demo server
(August 2026); the Google cassette is shaped per the API docs and labeled."""
import hashlib
import json
import os
import time
import urllib.parse
import urllib.request

from . import geo


class CircuitOpen(RuntimeError):
    pass


class ResilientClient:
    def __init__(self, name, transport=None, max_retries=3, backoff_s=0.5,
                 rate_limit_s=0.6, cassette=None, record_to=None,
                 fail_threshold=3, sleep_fn=time.sleep):
        self.name = name
        self.transport = transport or self._urllib_get
        self.max_retries = max_retries
        self.backoff_s = backoff_s
        self.rate_limit_s = rate_limit_s
        self.sleep_fn = sleep_fn
        self.cassette = dict(cassette) if cassette else None
        self.record_to = record_to
        self.recorded = {}
        self.fail_threshold = fail_threshold
        self.failures = 0
        self.open = False
        self.calls = 0
        self._last_call = 0.0

    @staticmethod
    def _urllib_get(url):
        req = urllib.request.Request(url, headers={"User-Agent": "dispatch-planner/0.1"})
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.read()

    @staticmethod
    def _key(url):
        return hashlib.sha256(url.encode()).hexdigest()[:20]

    def get(self, url):
        key = self._key(url)
        if self.cassette is not None:                      # replay mode
            if key not in self.cassette:
                raise KeyError(f"cassette miss for {self.name}: {url[:80]}")
            return self.cassette[key].encode()
        if self.open:
            raise CircuitOpen(f"{self.name} circuit open")
        wait = self.rate_limit_s - (time.monotonic() - self._last_call)
        if wait > 0:
            self.sleep_fn(wait)
        err = None
        for attempt in range(self.max_retries):
            try:
                self._last_call = time.monotonic()
                self.calls += 1
                body = self.transport(url)
                self.failures = 0
                if self.record_to is not None:
                    self.recorded[key] = body.decode()
                return body
            except Exception as e:                          # noqa: BLE001
                err = e
                self.sleep_fn(self.backoff_s * (2 ** attempt))
        self.failures += 1
        if self.failures >= self.fail_threshold:
            self.open = True
        raise err

    def save_cassette(self):
        if self.record_to:
            existing = {}
            if os.path.exists(self.record_to):
                existing = json.load(open(self.record_to))
            existing.update(self.recorded)
            with open(self.record_to, "w") as f:
                json.dump(existing, f, indent=0)


class TravelTimeProvider:
    name = "base"
    degraded = False

    def matrix(self, points):
        """points: [(lat, lon), ...] -> (durations_min NxN, distances_km NxN)"""
        raise NotImplementedError


class HaversineProvider(TravelTimeProvider):
    name = "haversine"

    def __init__(self, circuity=geo.ROAD_FACTOR):
        self.circuity = circuity

    def matrix(self, points):
        n = len(points)
        dur = [[0.0] * n for _ in range(n)]
        dist = [[0.0] * n for _ in range(n)]
        for i in range(n):
            for j in range(n):
                if i != j:
                    dist[i][j] = geo.road_km(points[i], points[j], self.circuity)
                    dur[i][j] = dist[i][j] / geo.SPEED_KMH * 60.0
        return dur, dist


class OSRMProvider(TravelTimeProvider):
    name = "osrm"

    def __init__(self, client=None, base_url=None, fallback=None):
        self.base = (base_url or os.environ.get("OSRM_URL")
                     or "https://router.project-osrm.org")
        self.client = client or ResilientClient("osrm")
        self.fallback = fallback or HaversineProvider()

    def matrix(self, points):
        coords = ";".join(f"{lon:.5f},{lat:.5f}" for lat, lon in points)
        url = f"{self.base}/table/v1/driving/{coords}?annotations=duration,distance"
        try:
            d = json.loads(self.client.get(url))
            if d.get("code") != "Ok":
                raise RuntimeError(f"OSRM said {d.get('code')}")
            dur = [[(x or 0) / 60.0 for x in row] for row in d["durations"]]
            dist = [[(x or 0) / 1000.0 for x in row] for row in d["distances"]]
            self.degraded = False
            return dur, dist
        except (CircuitOpen, Exception):                    # degrade, loudly flagged
            self.degraded = True
            return self.fallback.matrix(points)


class GoogleMatrixProvider(TravelTimeProvider):
    name = "google"

    def __init__(self, client=None, api_key=None, fallback=None):
        self.key = api_key or os.environ.get("GOOGLE_MAPS_API_KEY", "")
        self.client = client or ResilientClient("google")
        self.fallback = fallback or HaversineProvider()

    def matrix(self, points):
        if not self.key and self.client.cassette is None:
            self.degraded = True
            return self.fallback.matrix(points)
        locs = "|".join(f"{lat:.5f},{lon:.5f}" for lat, lon in points)
        url = ("https://maps.googleapis.com/maps/api/distancematrix/json?"
               + urllib.parse.urlencode({"origins": locs, "destinations": locs,
                                         "key": self.key or "CASSETTE"}))
        try:
            d = json.loads(self.client.get(url))
            dur = [[el["duration"]["value"] / 60.0 for el in row["elements"]]
                   for row in d["rows"]]
            dist = [[el["distance"]["value"] / 1000.0 for el in row["elements"]]
                    for row in d["rows"]]
            self.degraded = False
            return dur, dist
        except (CircuitOpen, Exception):
            self.degraded = True
            return self.fallback.matrix(points)
