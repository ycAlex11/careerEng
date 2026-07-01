"""Bridge solution requests to an LLM proposal writer.

This module is intentionally narrow: it packages an existing
``solution_request.md`` and evidence pack for a provider, validates the JSON
proposal returned by that provider, and writes ``proposal.json``. It does not
invent workflow strategy in Python.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from careereng.action_cards import ActionCardError, ActionCardStore
from careereng.evolution.proposals import EvolutionProposalError, proposal_path_for_run, validate_proposal
from careereng.providers.base import LLMProvider, ProviderError
from careereng.utils import now_iso, read_json, write_json


class EvolutionSolutionBridgeError(ValueError):
    """Raised when a solution request cannot be converted into a proposal."""


class ProviderSolutionBridge:
    """Use the configured LLM provider to write a concrete proposal JSON."""

    def __init__(
        self,
        *,
        project_root: Path | str,
        workspace: Path | str,
        provider: LLMProvider,
        model: str,
        max_solution_request_chars: int = 24000,
        max_evidence_pack_chars: int = 60000,
        max_schema_chars: int = 18000,
    ):
        self.project_root = Path(project_root)
        self.workspace = Path(workspace)
        self.provider = provider
        self.model = str(model or "")
        self.max_solution_request_chars = max(4000, int(max_solution_request_chars or 24000))
        self.max_evidence_pack_chars = max(8000, int(max_evidence_pack_chars or 60000))
        self.max_schema_chars = max(4000, int(max_schema_chars or 18000))

    def write_proposal_for_run(self, run_id: str) -> dict[str, Any]:
        normalized_run_id = str(run_id or "").strip()
        if not normalized_run_id:
            raise EvolutionSolutionBridgeError("Missing evolution run id.")
        run_dir = self.workspace / "evolution" / "runs" / normalized_run_id
        run_json = run_dir / "run.json"
        run_payload = read_json(run_json)
        if not run_payload:
            raise EvolutionSolutionBridgeError(f"Missing run.json for evolution run: {normalized_run_id}")
        proposal_path = proposal_path_for_run(run_dir)
        if proposal_path.exists():
            proposal = read_json(proposal_path)
            if not isinstance(proposal, dict) or not proposal:
                raise EvolutionSolutionBridgeError(f"Invalid existing proposal: {proposal_path}")
            validate_proposal(proposal)
            self._mark_action_card_proposal_status(
                run_payload=run_payload,
                proposal_path=proposal_path,
                status="proposal_exists",
            )
            return {
                "run_id": normalized_run_id,
                "status": "proposal_exists",
                "proposal_output_path": str(proposal_path),
            }

        outputs = run_payload.get("outputs") if isinstance(run_payload.get("outputs"), dict) else {}
        solution_request = self._resolve_run_path(run_dir, outputs.get("solution_request") or "solution_request.md")
        evidence_pack = self._resolve_run_path(run_dir, outputs.get("evidence_pack") or "evidence_pack.md")
        schema_path = self.project_root / "docs" / "evolution" / "PROPOSAL_SCHEMA.md"
        if not solution_request.exists():
            raise EvolutionSolutionBridgeError(f"Missing solution request: {solution_request}")

        request_text = _read_text_limited(solution_request, self.max_solution_request_chars)
        evidence_text = _read_text_limited(evidence_pack, self.max_evidence_pack_chars) if evidence_pack.exists() else ""
        schema_text = _read_text_limited(schema_path, self.max_schema_chars) if schema_path.exists() else ""
        messages = self._build_messages(
            run_payload=run_payload,
            solution_request=request_text,
            evidence_pack=evidence_text,
            schema=schema_text,
        )
        chat_json = getattr(self.provider, "chat_json", None)
        try:
            if callable(chat_json):
                result = chat_json(
                    messages,
                    model=self.model,
                    schema=_proposal_json_schema(),
                    schema_name="evolution_proposal",
                )
                proposal = result.data if isinstance(result.data, dict) else {}
            else:
                raw = self.provider.chat(messages, model=self.model)
                proposal = LLMProvider.parse_json_object(raw) or {}
        except ProviderError as exc:
            raise EvolutionSolutionBridgeError(f"Solution provider failed: {exc}") from exc
        except Exception as exc:
            raise EvolutionSolutionBridgeError(f"Solution provider failed: {exc}") from exc

        if not isinstance(proposal, dict) or not proposal:
            raise EvolutionSolutionBridgeError("Solution provider did not return a JSON proposal.")
        _validate_run_binding(proposal=proposal, run_payload=run_payload)
        try:
            validate_proposal(proposal)
        except EvolutionProposalError as exc:
            raise EvolutionSolutionBridgeError(str(exc)) from exc
        proposal_path.parent.mkdir(parents=True, exist_ok=True)
        write_json(proposal_path, proposal)
        self._mark_action_card_proposal_status(
            run_payload=run_payload,
            proposal_path=proposal_path,
            status="proposal_written",
        )
        return {
            "run_id": normalized_run_id,
            "status": "proposal_written",
            "proposal_output_path": str(proposal_path),
        }

    def _mark_action_card_proposal_status(
        self,
        *,
        run_payload: dict[str, Any],
        proposal_path: Path,
        status: str,
    ) -> None:
        context = run_payload.get("context") if isinstance(run_payload.get("context"), dict) else {}
        card_id = str(context.get("action_card_id") or "").strip()
        if not card_id:
            return
        try:
            ActionCardStore(self.workspace).update_card_metadata(
                card_id,
                metadata={
                    "proposal_status": str(status or ""),
                    "proposal_written_at": now_iso(),
                    "proposal_output_path": str(proposal_path),
                    "solution_run_id": str(run_payload.get("run_id") or ""),
                },
                summary=f"Evolution proposal status updated: {status}.",
            )
        except ActionCardError:
            return

    def _build_messages(
        self,
        *,
        run_payload: dict[str, Any],
        solution_request: str,
        evidence_pack: str,
        schema: str,
    ) -> list[dict[str, str]]:
        system = "\n".join(
            [
                "You write CareerEng evolution proposals.",
                "Return one strict JSON object only. Do not use markdown.",
                "Do not propose Python code, provider changes, browser protocol patches, security changes, or schema migrations.",
                "Python only validates and applies; the strategy must be expressed as supported proposal changes.",
                "Start from the evolution strategy router, candidate spec, and evidence index.",
                "Treat Python-provided excerpts as starter context only; choose the evidence that matters from indexed paths.",
                "Use run_local_overlay only for short-horizon validation.",
                "For outer-loop synthesis, use the routed evolution spec and evidence pack to decide the supported proposal changes.",
                "Do not encode site workflow, matching policy, or form-filling strategy as Python changes.",
            ]
        )
        user = {
            "run_id": str(run_payload.get("run_id") or ""),
            "candidate_id": str(run_payload.get("candidate_id") or ""),
            "context": run_payload.get("context") if isinstance(run_payload.get("context"), dict) else {},
            "required_output_path": str(proposal_path_for_run(self.workspace / "evolution" / "runs" / str(run_payload.get("run_id") or ""))),
            "solution_request_md": solution_request,
            "evidence_pack_md": evidence_pack,
            "proposal_schema_md": schema,
            "instruction": (
                "Synthesize a concrete proposal from the routed strategy spec and evidence index. "
                "The top-level run_id and candidate_id must exactly match the provided values."
            ),
        }
        import json

        return [
            {"role": "system", "content": system},
            {"role": "user", "content": json.dumps(user, ensure_ascii=False)},
        ]

    @staticmethod
    def _resolve_run_path(run_dir: Path, value: Any) -> Path:
        path = Path(str(value or ""))
        if path.is_absolute():
            return path
        return run_dir / path


def _read_text_limited(path: Path, max_chars: int) -> str:
    text = path.read_text(encoding="utf-8")
    limit = max(1, int(max_chars or 1))
    if len(text) <= limit:
        return text
    head = text[: limit // 2]
    tail = text[-(limit // 2) :]
    return f"{head}\n\n[... truncated for provider context ...]\n\n{tail}"


def _validate_run_binding(*, proposal: dict[str, Any], run_payload: dict[str, Any]) -> None:
    run_id = str(run_payload.get("run_id") or "")
    candidate_id = str(run_payload.get("candidate_id") or "")
    if str(proposal.get("run_id") or "") != run_id:
        raise EvolutionSolutionBridgeError("Proposal run_id does not match solution run.")
    if str(proposal.get("candidate_id") or "") != candidate_id:
        raise EvolutionSolutionBridgeError("Proposal candidate_id does not match solution run.")


def _proposal_json_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": True,
        "required": ["run_id", "candidate_id", "diagnosis", "proposed_changes"],
        "properties": {
            "run_id": {"type": "string"},
            "candidate_id": {"type": "string"},
            "diagnosis": {"type": "string"},
            "proposed_changes": {
                "type": "array",
                "items": {"type": "object", "additionalProperties": True},
                "minItems": 1,
            },
            "evaluation_plan": {"type": "array", "items": {"type": "string"}},
            "risk_notes": {"type": "array", "items": {"type": "string"}},
        },
    }
