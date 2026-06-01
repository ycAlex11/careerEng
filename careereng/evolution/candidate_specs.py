"""Load evolution candidate specs from Markdown files."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from careereng.utils import parse_front_matter


DEFAULT_CANDIDATE_SPECS_DIR = Path("docs") / "evolution" / "candidates"
REQUIRED_FRONT_MATTER_FIELDS = ("id", "name", "target_type", "target_ref", "risk_level", "apply_policy")


class CandidateSpecError(ValueError):
    """Raised when an evolution candidate spec is invalid."""


@dataclass(frozen=True)
class CandidateSpec:
    id: str
    name: str
    target_type: str
    target_ref: str
    risk_level: str
    apply_policy: str
    path: Path
    body: str
    front_matter: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["path"] = str(self.path)
        return data


def candidate_specs_dir(project_root: Path | str) -> Path:
    return Path(project_root) / DEFAULT_CANDIDATE_SPECS_DIR


def load_candidate_specs(project_root: Path | str, *, specs_dir: Path | str | None = None) -> list[CandidateSpec]:
    root = Path(project_root)
    directory = Path(specs_dir) if specs_dir is not None else candidate_specs_dir(root)
    if not directory.exists():
        return []
    specs: list[CandidateSpec] = []
    for path in sorted(directory.glob("*.md")):
        if path.name.upper() == "README.MD":
            continue
        specs.append(load_candidate_spec(path))
    return sorted(specs, key=lambda spec: spec.id)


def get_candidate_spec(project_root: Path | str, candidate_id: str, *, specs_dir: Path | str | None = None) -> CandidateSpec:
    wanted = str(candidate_id or "").strip()
    for spec in load_candidate_specs(project_root, specs_dir=specs_dir):
        if spec.id == wanted:
            return spec
    raise CandidateSpecError(f"Unknown evolution candidate: {wanted or '<empty>'}")


def load_candidate_spec(path: Path | str) -> CandidateSpec:
    spec_path = Path(path)
    text = spec_path.read_text(encoding="utf-8")
    front_matter, body = parse_front_matter(text)
    missing = [field for field in REQUIRED_FRONT_MATTER_FIELDS if not str(front_matter.get(field) or "").strip()]
    if missing:
        raise CandidateSpecError(f"{spec_path}: missing required front matter field(s): {', '.join(missing)}")
    return CandidateSpec(
        id=str(front_matter["id"]).strip(),
        name=str(front_matter["name"]).strip(),
        target_type=str(front_matter["target_type"]).strip(),
        target_ref=str(front_matter["target_ref"]).strip(),
        risk_level=str(front_matter["risk_level"]).strip(),
        apply_policy=str(front_matter["apply_policy"]).strip(),
        path=spec_path,
        body=body.strip(),
        front_matter=dict(front_matter),
    )
