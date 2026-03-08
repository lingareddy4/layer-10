from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
from difflib import SequenceMatcher
from typing import Any

from .utils import normalize_space, parse_ts, stable_hash, unique, utc_now_iso


EXCLUSIVE_PREDICATES = {"issue_status", "issue_assignee"}
COMPONENT_ALIASES = {
    "docs": "documentation",
    "doc": "documentation",
    "documentation": "documentation",
    "commandline": "cli",
    "command-line": "cli",
    "command line": "cli",
}


def _artifact_dedup(
    artifacts: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, str], list[dict[str, Any]]]:
    alias_map: dict[str, str] = {}
    merges: list[dict[str, Any]] = []
    canonical_by_text: dict[str, str] = {}
    artifacts_by_id = {a["artifact_id"]: a for a in artifacts}

    for artifact in artifacts:
        normalized = normalize_space((artifact.get("text") or "").lower())
        key = stable_hash([artifact["artifact_type"], normalized], length=24)
        canonical_id = canonical_by_text.get(key)
        if canonical_id is None:
            canonical_by_text[key] = artifact["artifact_id"]
            alias_map[artifact["artifact_id"]] = artifact["artifact_id"]
        else:
            alias_map[artifact["artifact_id"]] = canonical_id
            merges.append(
                {
                    "merge_id": "m_art_" + stable_hash([canonical_id, artifact["artifact_id"]], 16),
                    "merge_type": "artifact_exact_duplicate",
                    "from_id": artifact["artifact_id"],
                    "to_id": canonical_id,
                    "reason": "Exact normalized text match",
                    "reversible": True,
                    "merged_at": utc_now_iso(),
                }
            )

    issue_groups: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for artifact in artifacts:
        if artifact["artifact_id"] != alias_map[artifact["artifact_id"]]:
            continue
        if artifact.get("artifact_type") not in {"comment", "issue_body"}:
            continue
        issue_groups[int(artifact["issue_number"])].append(artifact)

    for issue_number, group in issue_groups.items():
        for i, left in enumerate(group):
            if alias_map[left["artifact_id"]] != left["artifact_id"]:
                continue
            left_text = normalize_space((left.get("text") or "").lower())
            if len(left_text) < 80:
                continue
            for right in group[i + 1 :]:
                if alias_map[right["artifact_id"]] != right["artifact_id"]:
                    continue
                right_text = normalize_space((right.get("text") or "").lower())
                if abs(len(left_text) - len(right_text)) > 30:
                    continue
                score = SequenceMatcher(None, left_text, right_text).ratio()
                if score >= 0.97:
                    alias_map[right["artifact_id"]] = left["artifact_id"]
                    merges.append(
                        {
                            "merge_id": "m_art_" + stable_hash(
                                [left["artifact_id"], right["artifact_id"]], 16
                            ),
                            "merge_type": "artifact_near_duplicate",
                            "from_id": right["artifact_id"],
                            "to_id": left["artifact_id"],
                            "reason": f"Near-duplicate text in issue #{issue_number} (similarity={score:.3f})",
                            "reversible": True,
                            "merged_at": utc_now_iso(),
                        }
                    )

    canonical_artifacts = [
        deepcopy(artifacts_by_id[artifact_id])
        for artifact_id in artifacts_by_id
        if alias_map.get(artifact_id, artifact_id) == artifact_id
    ]
    return canonical_artifacts, alias_map, merges


def _entity_key(entity: dict[str, Any]) -> tuple[str, str]:
    entity_type = entity["entity_type"]
    name = normalize_space(entity.get("name") or "").lower()
    if entity_type == "person":
        name = name.lstrip("@")
    if entity_type == "component":
        name = COMPONENT_ALIASES.get(name, name)
    return entity_type, name


def _entity_canonicalization(
    entities: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, str], list[dict[str, Any]]]:
    alias_map: dict[str, str] = {}
    merges: list[dict[str, Any]] = []
    canonical_index: dict[tuple[str, str], str] = {}
    canonical_entities: dict[str, dict[str, Any]] = {}

    for entity in entities:
        entity_id = entity["entity_id"]
        key = _entity_key(entity)
        canonical_id = canonical_index.get(key)
        if canonical_id is None:
            canonical_index[key] = entity_id
            alias_map[entity_id] = entity_id
            canonical_entities[entity_id] = deepcopy(entity)
            canonical_entities[entity_id]["aliases"] = unique(
                [normalize_space(entity.get("name") or "")]
                + [normalize_space(alias) for alias in entity.get("aliases", []) if alias]
            )
        else:
            alias_map[entity_id] = canonical_id
            merged = canonical_entities[canonical_id]
            merged["aliases"] = unique(
                merged.get("aliases", [])
                + [normalize_space(entity.get("name") or "")]
                + [normalize_space(alias) for alias in entity.get("aliases", []) if alias]
            )
            merges.append(
                {
                    "merge_id": "m_ent_" + stable_hash([canonical_id, entity_id], 16),
                    "merge_type": "entity_canonicalization",
                    "from_id": entity_id,
                    "to_id": canonical_id,
                    "reason": "Same normalized entity key",
                    "reversible": True,
                    "merged_at": utc_now_iso(),
                }
            )

    ordered = sorted(canonical_entities.values(), key=lambda item: (item["entity_type"], item["name"]))
    return ordered, alias_map, merges


def _apply_entity_aliases(
    claims: list[dict[str, Any]], entity_alias_map: dict[str, str]
) -> list[dict[str, Any]]:
    remapped: list[dict[str, Any]] = []
    for claim in claims:
        item = deepcopy(claim)
        item["subject_id"] = entity_alias_map.get(item["subject_id"], item["subject_id"])
        if item.get("object_type") == "entity" and item.get("object_id"):
            item["object_id"] = entity_alias_map.get(item["object_id"], item["object_id"])
        remapped.append(item)
    return remapped


def _claim_dedup(
    claims: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    grouped: dict[tuple[str, ...], dict[str, Any]] = {}
    merges: list[dict[str, Any]] = []

    for claim in claims:
        object_key = claim.get("object_id") or claim.get("object_value") or ""
        bucket_time = ""
        if claim["predicate"] in {"issue_status", "issue_assignee", "issue_has_label"}:
            event_time = claim.get("event_time") or ""
            bucket_time = event_time[:10]
        key = (
            claim["subject_id"],
            claim["predicate"],
            claim["object_type"],
            object_key,
            bucket_time,
        )
        existing = grouped.get(key)
        if existing is None:
            grouped[key] = deepcopy(claim)
            grouped[key]["evidence_ids"] = unique(claim.get("evidence_ids", []))
            continue

        existing["evidence_ids"] = unique(existing["evidence_ids"] + claim.get("evidence_ids", []))
        existing["confidence"] = max(existing["confidence"], claim["confidence"])
        if (claim.get("valid_from") or "") < (existing.get("valid_from") or "~"):
            existing["valid_from"] = claim.get("valid_from")
        if (claim.get("event_time") or "") < (existing.get("event_time") or "~"):
            existing["event_time"] = claim.get("event_time")

        merges.append(
            {
                "merge_id": "m_clm_" + stable_hash([existing["claim_id"], claim["claim_id"]], 16),
                "merge_type": "claim_duplicate",
                "from_id": claim["claim_id"],
                "to_id": existing["claim_id"],
                "reason": "Same canonical claim key",
                "reversible": True,
                "merged_at": utc_now_iso(),
            }
        )

    deduped_claims = list(grouped.values())
    return deduped_claims, merges


def _apply_temporal_state(claims: list[dict[str, Any]]) -> None:
    index: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for claim in claims:
        if claim["predicate"] in EXCLUSIVE_PREDICATES:
            index[(claim["subject_id"], claim["predicate"])].append(claim)

    for _, group in index.items():
        group.sort(key=lambda claim: parse_ts(claim.get("event_time")) or parse_ts(claim.get("valid_from")))
        current_value: str | None = None
        prev_claim: dict[str, Any] | None = None
        for claim in group:
            obj_value = claim.get("object_id") or claim.get("object_value")
            if prev_claim and current_value != obj_value:
                prev_claim["valid_to"] = claim.get("event_time") or claim.get("valid_from")
                prev_claim["is_current"] = False
            current_value = obj_value
            prev_claim = claim

        if group:
            for claim in group[:-1]:
                if claim.get("valid_to"):
                    claim["is_current"] = False
            group[-1]["is_current"] = True


def _evidence_dedup(
    evidences: list[dict[str, Any]],
    artifact_alias_map: dict[str, str],
) -> tuple[list[dict[str, Any]], dict[str, str]]:
    seen: dict[str, str] = {}
    alias_map: dict[str, str] = {}
    canonical: list[dict[str, Any]] = []

    for evidence in evidences:
        item = deepcopy(evidence)
        item["artifact_id"] = artifact_alias_map.get(item["artifact_id"], item["artifact_id"])
        key = stable_hash(
            [
                item["artifact_id"],
                item.get("excerpt") or "",
                str(item.get("char_start")),
                str(item.get("char_end")),
                item.get("timestamp") or "",
            ],
            length=24,
        )
        canonical_id = seen.get(key)
        if canonical_id is None:
            seen[key] = item["evidence_id"]
            alias_map[item["evidence_id"]] = item["evidence_id"]
            canonical.append(item)
        else:
            alias_map[item["evidence_id"]] = canonical_id

    return canonical, alias_map


def _apply_evidence_aliases(
    claims: list[dict[str, Any]], evidence_alias_map: dict[str, str]
) -> list[dict[str, Any]]:
    updated: list[dict[str, Any]] = []
    for claim in claims:
        item = deepcopy(claim)
        item["evidence_ids"] = unique(
            [evidence_alias_map.get(ev_id, ev_id) for ev_id in item.get("evidence_ids", [])]
        )
        updated.append(item)
    return updated


def deduplicate_and_canonicalize(
    corpus: dict[str, Any], extraction: dict[str, Any]
) -> dict[str, Any]:
    canonical_artifacts, artifact_alias_map, artifact_merges = _artifact_dedup(corpus["artifacts"])
    canonical_entities, entity_alias_map, entity_merges = _entity_canonicalization(extraction["entities"])
    remapped_claims = _apply_entity_aliases(extraction["claims"], entity_alias_map)
    deduped_claims, claim_merges = _claim_dedup(remapped_claims)
    _apply_temporal_state(deduped_claims)
    canonical_evidence, evidence_alias_map = _evidence_dedup(
        extraction["evidences"], artifact_alias_map
    )
    remapped_claims = _apply_evidence_aliases(deduped_claims, evidence_alias_map)

    return {
        "meta": {
            "deduped_at": utc_now_iso(),
            "artifacts_before": len(corpus["artifacts"]),
            "artifacts_after": len(canonical_artifacts),
            "entities_before": len(extraction["entities"]),
            "entities_after": len(canonical_entities),
            "claims_before": len(extraction["claims"]),
            "claims_after": len(remapped_claims),
            "evidence_before": len(extraction["evidences"]),
            "evidence_after": len(canonical_evidence),
        },
        "artifacts": canonical_artifacts,
        "entities": canonical_entities,
        "claims": remapped_claims,
        "evidences": canonical_evidence,
        "alias_maps": {
            "artifact_aliases": artifact_alias_map,
            "entity_aliases": entity_alias_map,
            "evidence_aliases": evidence_alias_map,
        },
        "merge_log": {
            "artifact_merges": artifact_merges,
            "entity_merges": entity_merges,
            "claim_merges": claim_merges,
        },
    }

