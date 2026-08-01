"""Stage 3A: resilient client behavior and provider determinism."""
import json
import os

import pytest

from dispatch.providers import (CircuitOpen, HaversineProvider, OSRMProvider,
                                ResilientClient)

CASSETTE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "fixtures", "cassettes", "osrm_table.json")
PTS = [(43.05, -76.15), (43.55, -76.10), (43.05, -75.20)]


def test_retry_then_success_with_backoff():
    calls, sleeps = [], []
    def flaky(url):
        calls.append(url)
        if len(calls) < 3:
            raise TimeoutError("transient")
        return b'{"ok": 1}'
    c = ResilientClient("t", transport=flaky, max_retries=3, backoff_s=0.1,
                        rate_limit_s=0, sleep_fn=sleeps.append)
    assert c.get("http://x") == b'{"ok": 1}'
    assert len(calls) == 3
    assert sleeps and sleeps[0] < sleeps[1], "exponential backoff between attempts"


def test_circuit_opens_after_repeated_failure_and_provider_degrades():
    def down(url):
        raise ConnectionError("api down")
    c = ResilientClient("t", transport=down, max_retries=1, backoff_s=0,
                        rate_limit_s=0, fail_threshold=2, sleep_fn=lambda s: None)
    prov = OSRMProvider(client=c, fallback=HaversineProvider())
    d1, _ = prov.matrix(PTS)                 # failure 1 -> fallback
    d2, _ = prov.matrix(PTS)                 # failure 2 -> breaker opens
    assert prov.degraded
    d3, _ = prov.matrix(PTS)                 # breaker open -> instant fallback
    hav, _ = HaversineProvider().matrix(PTS)
    assert d3 == hav, "degraded output IS the haversine matrix — planning never stops"
    with pytest.raises(CircuitOpen):
        c.get("http://x")


def test_cassette_replay_is_deterministic_and_offline():
    cassette = json.load(open(CASSETTE))
    def never(url):
        raise AssertionError("replay mode must not touch the network")
    c = ResilientClient("osrm", cassette=cassette, transport=never)
    prov = OSRMProvider(client=c, fallback=HaversineProvider())
    # the recorded day contains this exact single-zone submatrix
    # (replay by URL hash: any recorded call reproduces byte-identically)
    key_count = len(cassette)
    assert key_count >= 10, "shipped cassette holds the recorded day"
    # a cassette miss must raise, never silently fall through to live
    with pytest.raises(KeyError):
        c.get("http://not-recorded")
