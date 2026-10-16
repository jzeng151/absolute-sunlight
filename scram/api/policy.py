"""Deterministic GREEN / YELLOW / RED (build spec sections 3 and 6).

A pure function of the typed action, the rulebook and the simulated reactor.
The model only produces the TypedAction; nothing it writes (intent, confidence
above the floor, a supplied hash) can choose the state.
"""

from api.contracts import PolicyDecision, Reactor, Rule, TypedAction

MIN_CONFIDENCE = 0.8
READ_ONLY_TOOLS = {"analyze_history"}
WRITE_TOOLS = {"adjust_output"}
SAFETY_TOOLS = {"disable_safety_function", "suppress_alarm", "bypass_interlock"}

RULE_REASON = {
    "NC-01": "READ_ONLY_ALLOWED",
    "NC-03": "HUMAN_APPROVAL_REQUIRED",
    "NC-04": "BOUNDARY_EXCEEDED",
    "NC-05": "SAFETY_BYPASS_FORBIDDEN",
    "NC-06": "NO_AUTHORITY",
}


def decide(action: TypedAction, rules: list[Rule], reactor: Reactor) -> PolicyDecision:
    def decision(state, rule_ids, reason_code, policy_version):
        return PolicyDecision(
            run_id=action.run_id,
            policy_version=policy_version,
            rule_ids=rule_ids,
            state=state,
            reason_code=reason_code,
            reversible=state != "RED",
            approval_required=state == "YELLOW",
        )

    version = rules[0].policy_version if rules else "unknown"
    if action.confidence < MIN_CONFIDENCE or not _well_formed(action, reactor):
        return decision("RED", [], "PARSE_UNCERTAIN", version)  # machine maps RED -> BLOCKED

    rule_id = _governing_rule(action, reactor)
    rule = next((r for r in rules if r.rule_id == rule_id), None)
    if rule is None:  # fail closed: a rule we can't find grants nothing
        return decision("RED", ["NC-06"], "NO_AUTHORITY", version)
    return decision(rule.default_state, [rule.rule_id], RULE_REASON[rule.rule_id], rule.policy_version)


def _governing_rule(action: TypedAction, reactor: Reactor) -> str:
    if action.tool in SAFETY_TOOLS:
        return "NC-05"
    if action.tool in READ_ONLY_TOOLS:
        return "NC-01"
    if action.tool in WRITE_TOOLS:
        requested = reactor.output_percent + action.normalized_parameters["delta_percent"]
        return "NC-03" if reactor.band_min <= requested <= reactor.band_max else "NC-04"
    return "NC-06"


def _well_formed(action: TypedAction, reactor: Reactor) -> bool:
    """A write must carry a numeric delta whose result matches its own postcondition."""
    if action.tool not in WRITE_TOOLS:
        return True
    delta = action.normalized_parameters.get("delta_percent")
    if isinstance(delta, bool) or not isinstance(delta, (int, float)):
        return False
    return action.expected_postcondition.get("output_percent") == reactor.output_percent + delta
