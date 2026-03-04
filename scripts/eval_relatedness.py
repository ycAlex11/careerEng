"""Offline relatedness evaluator."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from careereng.agent.relatedness import RelatednessEvaluator


class DummyProvider:
    def chat(self, messages, *, model):
        return "{}"


def main() -> int:
    dataset = ROOT / "evals" / "relatedness" / "dataset.jsonl"
    if not dataset.exists():
        legacy = ROOT / "evals" / "relatedness_dataset.jsonl"
        if legacy.exists():
            dataset = legacy
    if not dataset.exists():
        print("dataset not found")
        return 1

    evaluator = RelatednessEvaluator(ROOT / "evals", threshold=0.7)
    provider = DummyProvider()

    total = 0
    ok = 0
    for line in dataset.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        row = json.loads(line)
        total += 1
        result = evaluator.evaluate(
            provider=provider,
            model="dummy",
            message=row["message"],
            persona={},
            intent={},
        )
        match = (
            bool(result["is_profile_related"]) == bool(row.get("is_profile_related"))
            and bool(result["is_intent_related"]) == bool(row.get("is_intent_related"))
        )
        if match:
            ok += 1

    acc = ok / total if total else 0.0
    print(f"samples={total} correct={ok} accuracy={acc:.2f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
