"""Offline router evaluator."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from careereng.agent.route_decider import RouteDecider


class DummyProvider:
    def chat(self, messages, *, model):
        return "{}"


def _to_bool(value) -> bool:
    return bool(value)


def main() -> int:
    dataset = ROOT / "evals" / "router" / "dataset.jsonl"
    if not dataset.exists():
        print("router dataset not found")
        return 1

    decider = RouteDecider(provider=DummyProvider(), model="dummy", confidence_threshold=0.75)
    total = 0
    route_ok = 0
    param_total = 0
    param_ok = 0
    fallback_used = 0

    for line in dataset.read_text(encoding="utf-8").splitlines():
        row = json.loads(line)
        message = str(row.get("message") or "")
        expected_route = str(row.get("expected_route") or "chat")
        decision = decider.decide(message=message, persona={}, intent={})
        total += 1
        if bool(decision.get("fallback_used")):
            fallback_used += 1

        final_route = str(decision.get("final_route") or "")
        if final_route == expected_route:
            route_ok += 1

        if expected_route == "site":
            params = decision.get("final_params") if isinstance(decision.get("final_params"), dict) else {}
            expected_apply = row.get("expected_apply_requested")
            if expected_apply is not None:
                param_total += 1
                if _to_bool(params.get("apply_requested")) == _to_bool(expected_apply):
                    param_ok += 1

            expected_prefix = str(row.get("expected_base_url_prefix") or "")
            if expected_prefix:
                param_total += 1
                base_url = str(params.get("base_url") or "")
                if base_url.startswith(expected_prefix):
                    param_ok += 1

            expected_company = str(row.get("expected_company_contains") or "")
            if expected_company:
                param_total += 1
                company = str(params.get("company") or "")
                if expected_company in company:
                    param_ok += 1

    route_acc = route_ok / total if total else 0.0
    param_acc = param_ok / param_total if param_total else 0.0
    fallback_ratio = fallback_used / total if total else 0.0
    print(
        "samples=%d route_correct=%d route_acc=%.2f param_acc=%.2f fallback_ratio=%.2f"
        % (total, route_ok, route_acc, param_acc, fallback_ratio)
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
