from __future__ import annotations

import datetime as dt
from typing import Any

from .utils import parse_ts, tokenize, utc_now_iso


def _entity_name(entity_index: dict[str, dict[str, Any]], entity_id: str | None) -> str:
    if not entity_id:
        return ""
    entity = entity_index.get(entity_id)
    return entity.get("name", entity_id) if entity else entity_id


def _claim_text(
    claim: dict[str, Any],
    entity_index: dict[str, dict[str, Any]],
    evidence_index: dict[str, dict[str, Any]],
) -> str:
    subject = _entity_name(entity_index, claim.get("subject_id"))
    if claim.get("object_type") == "entity":
        obj = _entity_name(entity_index, claim.get("object_id"))
    else:
        obj = claim.get("object_value") or ""

    evidence_bits: list[str] = []
    for evidence_id in claim.get("evidence_ids", [])[:3]:
        ev = evidence_index.get(evidence_id)
        if ev:
            evidence_bits.append(ev.get("excerpt") or "")
    return " ".join([subject, claim["predicate"], obj] + evidence_bits)


def _score_claim(
    question_tokens: set[str],
    claim: dict[str, Any],
    claim_text: str,
) -> float:
    claim_tokens = set(tokenize(claim_text))
    overlap = question_tokens & claim_tokens
    if not question_tokens:
        lexical = 0.0
    else:
        lexical = len(overlap) / len(question_tokens)

    confidence = float(claim.get("confidence", 0.0))
    recency = 0.1
    event_time = parse_ts(claim.get("event_time"))
    if event_time:
        days_old = max((dt.datetime.now(dt.timezone.utc) - event_time).days, 0)
        recency = max(0.0, 1.0 - min(days_old / 365.0, 1.0))

    return round((0.55 * lexical) + (0.30 * confidence) + (0.15 * recency), 6)


def build_context_pack(
    graph: dict[str, Any],
    question: str,
    top_k: int = 8,
    max_expansion: int = 4,
) -> dict[str, Any]:
    entities = graph["entities"]
    claims = graph["claims"]
    evidences = graph["evidences"]
    artifacts = graph["artifacts"]

    entity_index = {entity["entity_id"]: entity for entity in entities}
    evidence_index = {evidence["evidence_id"]: evidence for evidence in evidences}
    artifact_index = {artifact["artifact_id"]: artifact for artifact in artifacts}

    q_tokens = set(tokenize(question))
    ranked: list[tuple[float, dict[str, Any]]] = []
    for claim in claims:
        text = _claim_text(claim, entity_index, evidence_index)
        score = _score_claim(q_tokens, claim, text)
        if score <= 0:
            continue
        ranked.append((score, claim))
    ranked.sort(key=lambda pair: pair[0], reverse=True)
    top_ranked = ranked[:top_k]

    expanded_claim_ids: set[str] = {claim["claim_id"] for _, claim in top_ranked}
    for _, claim in top_ranked:
        if max_expansion <= 0:
            break
        subject_id = claim["subject_id"]
        related = [
            item
            for item in claims
            if item["claim_id"] not in expanded_claim_ids and item["subject_id"] == subject_id
        ][:max_expansion]
        for related_claim in related:
            expanded_claim_ids.add(related_claim["claim_id"])
        max_expansion -= len(related)

    final_ranked: list[tuple[float, dict[str, Any]]] = []
    for score, claim in ranked:
        if claim["claim_id"] in expanded_claim_ids:
            final_ranked.append((score, claim))
        if len(final_ranked) >= top_k + 4:
            break

    ranked_items: list[dict[str, Any]] = []
    linked_entities: dict[str, dict[str, Any]] = {}

    for rank, (score, claim) in enumerate(final_ranked, start=1):
        citations: list[dict[str, Any]] = []
        for evidence_id in claim.get("evidence_ids", []):
            evidence = evidence_index.get(evidence_id)
            if not evidence:
                continue
            artifact = artifact_index.get(evidence["artifact_id"], {})
            citations.append(
                {
                    "evidence_id": evidence_id,
                    "source_artifact_id": evidence.get("artifact_id"),
                    "source_url": evidence.get("source_url"),
                    "source_type": evidence.get("artifact_type"),
                    "issue_number": artifact.get("issue_number"),
                    "timestamp": evidence.get("timestamp"),
                    "excerpt": evidence.get("excerpt"),
                    "char_start": evidence.get("char_start"),
                    "char_end": evidence.get("char_end"),
                }
            )

        subject_id = claim["subject_id"]
        linked_entities[subject_id] = entity_index.get(
            subject_id,
            {"entity_id": subject_id, "entity_type": "unknown", "name": subject_id},
        )
        if claim.get("object_type") == "entity" and claim.get("object_id"):
            obj_id = claim["object_id"]
            linked_entities[obj_id] = entity_index.get(
                obj_id,
                {"entity_id": obj_id, "entity_type": "unknown", "name": obj_id},
            )

        ranked_items.append(
            {
                "rank": rank,
                "score": round(score, 4),
                "claim": {
                    "claim_id": claim["claim_id"],
                    "subject_id": claim["subject_id"],
                    "subject_name": _entity_name(entity_index, claim["subject_id"]),
                    "predicate": claim["predicate"],
                    "object_type": claim["object_type"],
                    "object_id": claim.get("object_id"),
                    "object_name": _entity_name(entity_index, claim.get("object_id")),
                    "object_value": claim.get("object_value"),
                    "confidence": claim["confidence"],
                    "event_time": claim.get("event_time"),
                    "is_current": claim.get("is_current", True),
                },
                "citations": citations,
            }
        )

    return {
        "question": question,
        "generated_at": utc_now_iso(),
        "retrieval": {
            "method": "lexical+confidence+recency",
            "top_k": top_k,
            "expanded_result_count": len(ranked_items),
        },
        "ranked_items": ranked_items,
        "linked_entities": list(linked_entities.values()),
    }

