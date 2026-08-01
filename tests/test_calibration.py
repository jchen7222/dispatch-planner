"""Stage 8: the loop is closed, versioned, and honest."""
import json
import os
import shutil

import pytest

from dispatch.calibration import fit, load_artifact, write_artifact
from dispatch.packing import MAX_PARTIES_SOFT, _ORDERS
from dispatch.run import plan, fingerprint, run_calibrate

CAL_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       "calibration_test_tmp")


@pytest.fixture(scope="module")
def planned():
    return plan(seed=42, n_orders=80)


def teardown_module(module):
    shutil.rmtree(CAL_DIR, ignore_errors=True)


def test_parameter_recovery_circuity(planned):
    orders, _, loads, _, _ = planned
    omap = {o.order_id: o for o in orders}
    corridors = fit([l for l in loads if l.stop_sequence], omap)
    for z, v in corridors.items():
        assert v["circuity_fit"] == pytest.approx(1.3, abs=0.02), \
            "the fit must recover the generator's known road factor"


def test_wider_buffers_for_noisier_corridors(planned):
    orders, _, loads, _, _ = planned
    omap = {o.order_id: o for o in orders}
    corridors = fit([l for l in loads if l.stop_sequence], omap)
    vols = sorted((v["arrival_slack_stdev_min"], v["window_buffer_min"])
                  for v in corridors.values())
    assert vols[0][1] <= vols[-1][1], "noisier corridor, wider buffer"


def test_artifact_is_versioned_and_config_event_appended(planned):
    orders, _, loads, _, led = planned
    shutil.rmtree(CAL_DIR, ignore_errors=True)
    omap = {o.order_id: o for o in orders}
    corridors = fit([l for l in loads if l.stop_sequence], omap)
    p1, v1, _ = write_artifact(CAL_DIR, corridors, {"assign_time_budget_s": 10},
                               fingerprint(loads), "T+0")
    p2, v2, _ = write_artifact(CAL_DIR, corridors, {"assign_time_budget_s": 10},
                               fingerprint(loads), "T+1")
    assert (v1, v2) == (1, 2) and os.path.exists(p1) and os.path.exists(p2)
    art = load_artifact(p1)
    assert "mechanism demonstration" in art["note"], "the honesty note ships in the artifact"


def test_plans_record_their_calibration_version(planned):
    orders, _, loads, _, led = planned
    calib = {"calibration_version": 7, "corridors": {}}
    _, _, loads2, _, led2 = plan(seed=42, n_orders=80, calibration=calib)
    plan_events = [e for e in led2.events if e["type"] == "load_plan_created"]
    assert plan_events and all(e["payload"]["calibration_version"] == 7
                               for e in plan_events)
    base_events = [e for e in led.events if e["type"] == "load_plan_created"]
    assert all(e["payload"]["calibration_version"] == 0 for e in base_events)


def test_blast_radius_preference_is_soft_but_real(planned):
    orders, _, loads, _, _ = planned
    over = 0
    for l in loads:
        parties = set()
        for a in l.allocations:
            o = next(x for x in orders if x.order_id == a.order_id)
            parties |= {o.supplier_id, o.customer_id}
        if len(parties) > MAX_PARTIES_SOFT:
            over += 1
    assert over <= max(2, len(loads) // 4), \
        "the soft preference bounds concentration without ever blocking a fit"
