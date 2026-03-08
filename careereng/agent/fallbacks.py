"""Deterministic fallbacks for search and intent derivation."""

from __future__ import annotations

from typing import Any


def default_search_spec(user_message: str, persona: dict[str, Any], intent: dict[str, Any]) -> dict[str, Any]:
    roles = intent.get("target_roles") if isinstance(intent.get("target_roles"), list) else []
    locations = intent.get("target_locations") if isinstance(intent.get("target_locations"), list) else []
    date_posted_after = str(intent.get("date_posted_after") or "")
    company_preferences = intent.get("company_preferences") if isinstance(intent.get("company_preferences"), list) else []
    if not roles:
        skills = persona.get("skills") if isinstance(persona.get("skills"), dict) else {}
        programming = skills.get("programming") if isinstance(skills.get("programming"), list) else []
        if programming:
            roles = ["Software Engineer"]
    if not roles:
        roles = ["Software Engineer"]
    if not locations:
        locations = ["China"]
    primary_role = str(roles[0])
    primary_loc = str(locations[0])
    query = f"{primary_role} jobs {primary_loc}".strip()
    return {
        "query_text": query,
        "target_roles": roles[:5],
        "target_locations": locations[:8],
        "company_preferences": company_preferences[:6],
        "industry_preferences": intent.get("industry_preferences") if isinstance(intent.get("industry_preferences"), list) else [],
        "date_posted_after": date_posted_after,
        "must_have": intent.get("must_have") if isinstance(intent.get("must_have"), list) else [],
        "nice_to_have": intent.get("nice_to_have") if isinstance(intent.get("nice_to_have"), list) else [],
        "google_queries": [
            query,
            f"{primary_role} careers {primary_loc}",
            f"{primary_role} openings {primary_loc}",
        ],
        "resolved_by": {"query_text": "fallback", "target_roles": "intent", "target_locations": "intent"},
        "source_note": user_message,
    }


def minimal_intent_candidate_from_persona(persona: dict[str, Any]) -> dict[str, Any]:
    roles: list[str] = []
    skills = persona.get("skills") if isinstance(persona.get("skills"), dict) else {}
    ai_skills = skills.get("ai") if isinstance(skills.get("ai"), list) else []
    programming = skills.get("programming") if isinstance(skills.get("programming"), list) else []
    experience = persona.get("experience") if isinstance(persona.get("experience"), list) else []
    projects = persona.get("projects") if isinstance(persona.get("projects"), list) else []

    if ai_skills:
        roles.append("AI Engineer")
    if programming:
        roles.append("Software Engineer")
    if experience:
        roles.append("Backend Engineer")
    if projects and not roles:
        roles.append("Software Engineer")
    if not roles:
        roles.append("Software Engineer")

    dedup_roles: list[str] = []
    for role in roles:
        role = str(role).strip()
        if role and role not in dedup_roles:
            dedup_roles.append(role)

    locations: list[str] = []
    basic = persona.get("basic") if isinstance(persona.get("basic"), dict) else {}
    current_city = str(basic.get("current_city") or "").strip()
    if current_city:
        locations.append(current_city)

    constraints = persona.get("constraints") if isinstance(persona.get("constraints"), dict) else {}
    work_auth = str(constraints.get("work_auth") or "").strip().lower()
    if work_auth == "china":
        locations.append("China")

    dedup_locations: list[str] = []
    for loc in locations:
        loc = str(loc).strip()
        if loc and loc not in dedup_locations:
            dedup_locations.append(loc)

    patch: dict[str, Any] = {"target_roles": dedup_roles[:3]}
    if dedup_locations:
        patch["target_locations"] = dedup_locations[:6]
    return patch
