"""Append-only event ledger with compensating events and point-in-time folds.
No event is ever modified or deleted; a correction is a new event that
supersedes an earlier one by (entity_id, version)."""
import json

class Ledger:
    def __init__(self, path=None):
        self.path = path
        self.events = []

    def append(self, etype, entity_id, payload, event_time, record_time, corrects=None):
        ev = {
            "seq": len(self.events),
            "type": etype, "entity_id": entity_id,
            "payload": payload,
            "event_time": event_time, "record_time": record_time,
            "corrects_seq": corrects,
        }
        self.events.append(ev)
        if self.path:
            with open(self.path, "a") as f:
                f.write(json.dumps(ev) + "\n")
        return ev["seq"]

    def fold_current(self, as_of_record_time=None):
        """Current state per entity: last event wins; a compensating event
        supersedes the one it corrects. Point-in-time = pass as_of_record_time."""
        state, superseded = {}, set()
        evs = [e for e in self.events
               if as_of_record_time is None or e["record_time"] <= as_of_record_time]
        for e in evs:
            if e["corrects_seq"] is not None:
                superseded.add(e["corrects_seq"])
        for e in evs:
            if e["seq"] in superseded:
                continue
            state[e["entity_id"]] = e
        return state

    def history(self, entity_id):
        return [e for e in self.events if e["entity_id"] == entity_id]
