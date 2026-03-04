"""Candidate event pipeline and report triggers."""

from __future__ import annotations

from typing import Any

from careereng.agent.extractor import CandidateExtractor
from careereng.providers.base import LLMProvider
from careereng.storage.intent_store import IntentStore
from careereng.storage.profile_store import ProfileStore


class ProfilePipeline:
    def __init__(
        self,
        *,
        provider: LLMProvider,
        model: str,
        extractor: CandidateExtractor,
        profile_store: ProfileStore,
        intent_store: IntentStore,
    ):
        self.provider = provider
        self.model = model
        self.extractor = extractor
        self.profile_store = profile_store
        self.intent_store = intent_store

    def process_message(
        self,
        *,
        message_id: str,
        session_id: str,
        message: str,
        relatedness: dict[str, Any],
    ) -> dict[str, Any]:
        new_reports = []

        if relatedness.get("is_profile_related"):
            patch = self.extractor.extract_profile_patch(self.provider, self.model, message)
            self.profile_store.append_event(
                {
                    "name": "profile.candidate",
                    "message": message,
                    "message_id": message_id,
                    "session_id": session_id,
                    "related": True,
                    "confidence": relatedness.get("confidence", 0.0),
                    "reason": relatedness.get("reason", ""),
                    "patch": patch,
                    "status": "candidate",
                    "few_shot_version": relatedness.get("few_shot_version", "v1"),
                    "evaluator_version": relatedness.get("evaluator_version", "v1"),
                }
            )

        if relatedness.get("is_intent_related"):
            patch = self.extractor.extract_intent_patch(self.provider, self.model, message)
            self.intent_store.append_event(
                {
                    "name": "intent.candidate",
                    "message": message,
                    "message_id": message_id,
                    "session_id": session_id,
                    "related": True,
                    "confidence": relatedness.get("confidence", 0.0),
                    "reason": relatedness.get("reason", ""),
                    "patch": patch,
                    "status": "candidate",
                    "few_shot_version": relatedness.get("few_shot_version", "v1"),
                    "evaluator_version": relatedness.get("evaluator_version", "v1"),
                }
            )

        profile_report = self.profile_store.generate_report_if_ready(20)
        if profile_report:
            new_reports.append(profile_report)

        intent_report = self.intent_store.generate_report_if_ready(20)
        if intent_report:
            new_reports.append(intent_report)

        return {"new_reports": new_reports}
