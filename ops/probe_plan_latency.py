"""Repeatable, model-off end-to-end latency probe. All artifacts stay remote."""
import argparse
import asyncio
import json
import sys
from dataclasses import replace
from pathlib import Path
from time import perf_counter
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import stormshift_v2.service as module
from stormshift_v2.config import settings
from stormshift_v2.schemas import SessionCreate, Mode, SandboxUpdate, PlanRequest
from stormshift_v2.store import SessionStore
from stormshift_v2.stream import StreamManager


async def run(seed, data_root):
    data_root.mkdir(parents=True, exist_ok=True)
    service = module.SessionService(
        config=replace(settings, data_dir=data_root, llm_mode="off", voice_mode="off"),
        store=SessionStore(data_root / f"seed-{seed}.db"), streams=StreamManager())
    state = await service.create(SessionCreate(mode=Mode.sandbox, seed=seed, sandbox_flights=20))
    state = await service.update_sandbox(state.id, SandboxUpdate(weather_severity=4, runway_closure_min=20))
    measured = {}
    original = module.discover_minimal_counterexamples
    def timed(*args, **kwargs):
        start = perf_counter()
        result = original(*args, **kwargs)
        measured[args[1].profile.value] = {"seconds": perf_counter() - start,
                                         "evaluations": result.get("evaluations")}
        return result
    start = perf_counter()
    with patch.object(module, "discover_minimal_counterexamples", timed):
        result = await service.plan(state.id, PlanRequest())
    seconds = perf_counter() - start
    signatures = []
    for plan in result.plans:
        proof = result.decision_lab["falsification_by_plan"][plan.id]
        signatures.append({
            "profile": plan.profile.value,
            "assignments": sorted([row.model_dump(mode="json") for row in plan.assignments],
                                  key=lambda row: row["flight_id"]),
            "metrics": plan.report.raw_metrics,
            "witnesses": [{key: axis[key] for key in ("id", "status", "target_id", "delta", "failures")}
                          for axis in proof["axes"]],
            "nearest": proof["nearest_witness_id"],
        })
    print(json.dumps({"seed": seed, "flights": 20, "weather": 4, "closure_min": 20,
          "model": "off", "runs_per_candidate": 50, "seconds": seconds,
          "phase": result.phase.value, "candidates": len(result.plans),
          "falsification": measured, "signatures": signatures}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--label", required=True)
    args = parser.parse_args()
    if not args.label.replace("-", "").isalnum():
        raise SystemExit("label must be alphanumeric with hyphens")
    root = settings.root / "tmp" / ("planning-latency-" + args.label)
    for seed in (2030, 2031, 2032):
        asyncio.run(run(seed, root))
