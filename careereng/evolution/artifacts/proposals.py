"""Persistence owner for proposal JSON stored inside an evolution run."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from careereng.utils import ensure_dir, write_json


class EvolutionProposalArtifactStore:
    def proposal_path(self, run_dir: Path | str) -> Path:
        return Path(run_dir) / "proposals" / "proposal.json"

    def load_json(self, run_dir: Path | str) -> dict[str, Any]:
        path = self.proposal_path(run_dir)
        if not path.exists():
            raise FileNotFoundError(path)
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            raise ValueError(f"Invalid proposal JSON: {path}") from exc
        if not isinstance(payload, dict):
            raise ValueError(f"Proposal must be a JSON object: {path}")
        return payload

    def save_json(self, run_dir: Path | str, proposal: dict[str, Any]) -> Path:
        path = self.proposal_path(run_dir)
        ensure_dir(path.parent)
        write_json(path, proposal)
        return path
