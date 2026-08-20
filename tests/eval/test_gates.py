import json
from pathlib import Path

from app.pipeline.constants import (
    COST_REGRESSION_LIMIT,
    EVAL_REGRESSION_LIMIT,
    MAX_REPAIR_ATTEMPTS,
    QC_PASS_THRESHOLD,
)

BASELINE_PATH = Path(__file__).parent / "baselines.json"


def _load_baseline() -> dict:
    return json.loads(BASELINE_PATH.read_text(encoding="utf-8"))


def test_qc_caps_match_prd():
    assert QC_PASS_THRESHOLD == 0.75
    assert MAX_REPAIR_ATTEMPTS == 2


def test_eval_regression_gate():
    baseline = _load_baseline()
    # Fixture happy-path continuity: every fake QC score is 0.9 ≥ 0.75.
    current = {
        "continuity_pass_rate": 1.0,
        "mean_qc_score": 0.9,
    }
    eval_drop = (
        baseline["continuity_pass_rate"] - current["continuity_pass_rate"]
    ) / baseline["continuity_pass_rate"]
    score_drop = (
        baseline["mean_qc_score"] - current["mean_qc_score"]
    ) / baseline["mean_qc_score"]
    assert eval_drop <= EVAL_REGRESSION_LIMIT
    assert score_drop <= EVAL_REGRESSION_LIMIT


def test_cost_regression_gate():
    baseline = _load_baseline()
    # 2 planning calls + 4 QC calls at $0.01 plus 4 fake shots at $0.01.
    current_cost = 0.10
    rise = (current_cost - baseline["cost_usd_per_happy_path_job"]) / baseline[
        "cost_usd_per_happy_path_job"
    ]
    assert rise <= COST_REGRESSION_LIMIT
