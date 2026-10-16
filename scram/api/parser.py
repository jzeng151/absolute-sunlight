"""Request -> TypedAction. Fixture fallback now; Gemini slots in behind parse() at build step 8.

Whatever produces the action, the policy engine decides the state, never the parser.
"""

from api.contracts import TypedAction
from api.rules import load_cases, load_reactor

FIXTURE_CONFIDENCE = 0.97


def parse(case_id: str, run_id: str, actor_id: str) -> TypedAction:
    case = load_cases()[case_id]
    return TypedAction(
        run_id=run_id,
        actor_id=actor_id,
        tool=case["tool"],
        target=load_reactor().target,
        normalized_parameters=case["normalized_parameters"],
        intent=case["intent"],
        expected_postcondition=case["expected_postcondition"],
        confidence=FIXTURE_CONFIDENCE,
    )
