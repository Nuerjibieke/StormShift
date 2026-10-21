from copy import deepcopy

from stormshift_v2.argumentation import resolve_operational_claims
from stormshift_v2.schemas import DataMode
from stormshift_v2.scenarios import storm_scenario
from stormshift_v2.trusted_state import build_trusted_operational_state


def _claim(
    claim_id: str,
    value: int,
    *,
    age: int = 0,
    source: str = "Ground Operations",
    confidence: float = .95,
):
    return {
        "id": claim_id,
        "key": "gate.A9.available_minute",
        "value": value,
        "source": source,
        "age_min": age,
        "confidence": confidence,
        "critical": True,
    }


def test_grounded_argumentation_independently_selects_current_authoritative_claims():
    trusted = build_trusted_operational_state(
        storm_scenario(),
        DataMode.synthetic,
    )
    result = trusted["argumentation"]

    assert result["status"] == "resolved"
    assert result["conflict_free"] is True
    assert result["attack_count"] == 2
    assert result["unresolved_keys"] == []
    assert set(result["accepted_ids"]) >= {
        "gate:A2:availability",
        "flight:SK218:fuel:occ",
    }
    assert set(result["rejected_ids"]) == {
        "gate:A2:availability:aodb",
        "flight:SK218:fuel:flight-plan",
    }
    assert {
        (edge["from"], edge["to"])
        for edge in result["edges"]
    } == {
        ("gate:A2:availability", "gate:A2:availability:aodb"),
        ("flight:SK218:fuel:occ", "flight:SK218:fuel:flight-plan"),
    }


def test_evidential_tie_remains_undecided_and_forces_abstention():
    result = resolve_operational_claims([
        _claim("argument:left", 18),
        _claim("argument:right", 31),
    ])

    assert result["status"] == "blocked"
    assert result["accepted_ids"] == []
    assert result["rejected_ids"] == []
    assert result["undecided_ids"] == ["argument:left", "argument:right"]
    assert result["unresolved_keys"] == ["gate.A9.available_minute"]
    assert result["attack_count"] == 2
    assert result["conflict_free"] is True


def test_stale_unopposed_critical_claim_is_not_silently_accepted():
    claims = [_claim("argument:stale", 25, age=11)]
    original = deepcopy(claims)

    result = resolve_operational_claims(claims)

    assert result["status"] == "blocked"
    assert result["accepted_ids"] == []
    assert result["rejected_ids"] == ["argument:stale"]
    assert result["unresolved_keys"] == ["gate.A9.available_minute"]
    assert claims == original
