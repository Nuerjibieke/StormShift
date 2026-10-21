from stormshift_v2.optimizer import empty_report
from stormshift_v2.schemas import PlanCandidate, PlanProfile
from stormshift_v2.twin_trust import (
    FAMILYWISE_STOP_E_VALUE,
    assess_twin_trust,
    bernoulli_e_process,
)


def _plan(local_max: float, empirical: float = 0.76) -> PlanCandidate:
    report = empty_report()
    row: dict[str, float] = {
        "evaluation_samples": 25.0,
    }
    for milestone in ("eldt", "eibt", "tobt"):
        row[f"{milestone}_empirical_coverage"] = empirical
        row[f"{milestone}_exceedances"] = float(round(25 * (1 - empirical)))
        row[f"{milestone}_e_process"] = local_max
        row[f"{milestone}_e_process_max"] = local_max
    report.per_flight = {"ZZ901": row}
    return PlanCandidate(
        profile=PlanProfile.balanced,
        title="test",
        assignments=[],
        actions=[],
        report=report,
        score=0,
    )


def test_e_process_accumulates_anytime_valid_undercoverage_evidence():
    covered = bernoulli_e_process([True] * 25)
    undercovered = bernoulli_e_process([False] * 8 + [True] * 17)

    assert covered["max_e_value"] == 1
    assert undercovered["exceedances"] == 8
    assert undercovered["max_e_value"] > FAMILYWISE_STOP_E_VALUE


def test_local_warning_does_not_claim_a_familywise_alarm():
    result = assess_twin_trust(_plan(6.0))

    assert result["status"] == "watch"
    assert result["blocks_unattended_execution"] is False
    assert result["familywise_streams"] == 3
    assert result["familywise_e_value"] == 2.0
    assert all(row["status"] == "watch" for row in result["rows"])


def test_familywise_alarm_blocks_unattended_execution():
    result = assess_twin_trust(_plan(90.0, empirical=0.40))

    assert result["status"] == "drift"
    assert result["blocks_unattended_execution"] is True
    assert result["familywise_e_value"] == 30.0
    assert result["automation_mode"] == "human_required"
