"""Carrier eligibility: which services may carry a given commodity class.

Two independent facts decide it, and eligibility is their intersection:

  1. CARRIER CAPABILITY — `CarrierService.approved_commodity_classes`. What the
     shipping company is certified to accept. A fact about the carrier.
  2. BUSINESS RULE — `routing_rules` in config/network.yml. What we choose to
     send where, which may be stricter than the carrier's certification (weight
     bands, value thresholds, a carrier we no longer trust for a class).

The rule set is DATA, not code, and it is VERSIONED. Every decision records the
rule-set version and the rule id that produced it, so six weeks and four rule
edits later a mis-route is traceable to a rule rather than reconstructed by hand:

    where rule_set_version = 'v1' and matched_rule = 'R3'

gives you every consignment that went out under the same rule — the blast radius.

Terminology: Chinese forwarding says 敏感货 / 敏感线; the English trade does not
say "sensitive". Carriers publish PROHIBITED (never) and RESTRICTED (only under
conditions) lists, and a service is APPROVED, or not, for a commodity class.
"""
from dataclasses import dataclass, field


@dataclass(frozen=True)
class RoutingDecision:
    """The decision itself, stored as a fact — not just its effect on a column."""
    order_id: str
    commodity_class: str
    weight_kg: float
    eligible_services: tuple          # what the rules + approvals allowed
    rule_set_version: str
    matched_rule: str                 # rule id, or NO_RULE
    reason: str = ""                  # set only when nothing is eligible

    @property
    def ok(self):
        return bool(self.eligible_services)


def _matches(cond, order):
    """A rule condition is a dict of field -> literal or {lt,lte,gt,gte}."""
    for fieldname, want in cond.items():
        got = getattr(order, fieldname, None)
        if isinstance(want, dict):
            for op, v in want.items():
                if op == "lt" and not got < v:   return False
                if op == "lte" and not got <= v: return False
                if op == "gt" and not got > v:   return False
                if op == "gte" and not got >= v: return False
        elif got != want:
            return False
    return True


class RuleSet:
    """Ordered rules; first match wins. Order in the YAML is the precedence."""

    def __init__(self, version, rules, effective_from=""):
        self.version = version
        self.effective_from = effective_from
        self.rules = rules            # [{id, when, allow}]

    @classmethod
    def from_config(cls, cfg):
        rr = cfg.get("routing_rules") or {}
        return cls(str(rr.get("version", "v0")),
                   list(rr.get("rules", [])),
                   str(rr.get("effective_from", "")))

    def decide(self, order, services):
        """Return a RoutingDecision. Never raises — an order with no eligible
        service is a decision with a reason, not an exception, so it lands in
        the ledger like everything else."""
        for rule in self.rules:
            if _matches(rule.get("when", {}), order):
                allowed = set(rule.get("allow", []))
                eligible = tuple(
                    s.name for s in services
                    if s.name in allowed
                    and order.commodity_class in s.approved_commodity_classes)
                reason = ""
                if not eligible:
                    reason = ("prohibited" if not allowed else "no_approved_service")
                return RoutingDecision(
                    order_id=order.order_id,
                    commodity_class=order.commodity_class,
                    weight_kg=order.weight_kg,
                    eligible_services=eligible,
                    rule_set_version=self.version,
                    matched_rule=str(rule.get("id", "?")),
                    reason=reason)

        return RoutingDecision(
            order_id=order.order_id,
            commodity_class=order.commodity_class,
            weight_kg=order.weight_kg,
            eligible_services=(),
            rule_set_version=self.version,
            matched_rule="NO_RULE",
            reason="no_rule_for_commodity_class")


def decide_all(orders, services, ruleset, ledger=None, record_time=None):
    """Decide for every order, and emit each decision to the ledger if given.

    The ledger entry is what makes the decision auditable: it carries the inputs,
    the outcome, the rule-set version and the matched rule.
    """
    decisions = {}
    for o in orders:
        d = ruleset.decide(o, services)
        decisions[o.order_id] = d
        if ledger is not None:
            ledger.append(
                "routing_decided", o.order_id,
                {"commodity_class": d.commodity_class,
                 "weight_kg": d.weight_kg,
                 "eligible_services": list(d.eligible_services),
                 "rule_set_version": d.rule_set_version,
                 "matched_rule": d.matched_rule,
                 "reason": d.reason},
                event_time=record_time, record_time=record_time)
    return decisions
