"""Relatedness evaluator (few-shot + evaluator versions)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml

from careereng.providers.base import LLMProvider


PROFILE_KWS = {
    "简历", "学历", "经验", "技能", "项目", "国籍", "城市", "python", "java", "c++", "education", "experience"
}
INTENT_KWS = {
    "岗位",
    "职位",
    "地点",
    "城市",
    "远程",
    "onsite",
    "hybrid",
    "remote",
    "fulltime",
    "intern",
    "company",
    "apply",
    "投递",
    "外企",
    "大厂",
    "百人公司",
    "startup",
    "big tech",
}
IGNORE_KWS = {"你好", "早上好", "晚安", "天气", "翻译", "translate", "nba", "赛程"}


class RelatednessEvaluator:
    def __init__(self, evals_dir: Path | None = None, threshold: float = 0.7, *, skills_dir: Path | None = None):
        if evals_dir is None:
            evals_dir = skills_dir
        if evals_dir is None:
            evals_dir = Path(".")
        self.skills_dir = evals_dir
        self.threshold = threshold
        self.few_shot = self._load_yaml(evals_dir / "relatedness" / "few_shot.yaml")
        self.evaluator = self._load_yaml(evals_dir / "relatedness" / "evaluator.yaml")

    def _load_yaml(self, path: Path) -> dict[str, Any]:
        if not path.exists():
            return {"version": "v1", "examples": []}
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
        except Exception:
            return {"version": "v1", "examples": []}
        return data if isinstance(data, dict) else {"version": "v1", "examples": []}

    def _parse_json(self, text: str) -> dict[str, Any] | None:
        raw = text.strip()
        candidates = [raw]
        if raw.startswith("```"):
            start = raw.find("\n")
            end = raw.rfind("```")
            if start != -1 and end > start:
                candidates.append(raw[start + 1 : end].strip())
        for c in candidates:
            try:
                data = json.loads(c)
            except Exception:
                continue
            if isinstance(data, dict):
                return data
        return None

    def _heuristic(self, message: str) -> dict[str, Any]:
        lowered = message.lower()
        ignore_hit = any(kw in lowered for kw in IGNORE_KWS)
        if ignore_hit:
            return {
                "is_profile_related": False,
                "is_intent_related": False,
                "confidence": 0.2,
                "reason": "heuristic ignore keyword match",
            }

        profile_hit = any(kw in lowered for kw in PROFILE_KWS)
        intent_hit = any(kw in lowered for kw in INTENT_KWS)

        if profile_hit and intent_hit:
            conf = 0.9
        elif profile_hit or intent_hit:
            conf = 0.8
        else:
            conf = 0.3

        return {
            "is_profile_related": profile_hit and conf >= self.threshold,
            "is_intent_related": intent_hit and conf >= self.threshold,
            "confidence": conf,
            "reason": "heuristic keyword match",
        }

    def evaluate(
        self,
        *,
        provider: LLMProvider,
        model: str,
        message: str,
        persona: dict[str, Any],
        intent: dict[str, Any],
    ) -> dict[str, Any]:
        prompt = (
            "Classify whether the message is related to profile(persona.md) and/or intent(intent.md). "
            "Return JSON only with keys: is_profile_related, is_intent_related, confidence, reason."
        )
        few = self.few_shot.get("examples", [])
        few_text = json.dumps(few[:10], ensure_ascii=False)
        messages = [
            {"role": "system", "content": prompt},
            {"role": "system", "content": f"few_shot_examples={few_text}"},
            {"role": "user", "content": f"persona={json.dumps(persona, ensure_ascii=False)}"},
            {"role": "user", "content": f"intent={json.dumps(intent, ensure_ascii=False)}"},
            {"role": "user", "content": f"message={message}"},
        ]

        parsed = None
        try:
            text = provider.chat(messages, model=model)
            parsed = self._parse_json(text)
        except Exception:
            parsed = None

        if not parsed:
            parsed = self._heuristic(message)

        confidence = float(parsed.get("confidence", 0.0) or 0.0)
        return {
            "is_profile_related": bool(parsed.get("is_profile_related") and confidence >= self.threshold),
            "is_intent_related": bool(parsed.get("is_intent_related") and confidence >= self.threshold),
            "confidence": confidence,
            "reason": str(parsed.get("reason") or ""),
            "few_shot_version": str(self.few_shot.get("version", "v1")),
            "evaluator_version": str(self.evaluator.get("version", "v1")),
        }
