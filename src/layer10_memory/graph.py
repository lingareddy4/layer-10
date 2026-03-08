from __future__ import annotations

from collections import Counter
from typing import Any

from .utils import stable_hash, utc_now_iso


def _literal_node_id(value: str) -> str:
    return "literal:" + stable_hash([value], length=20)


def build_memory_graph(
    corpus: dict[str, Any],
    deduped: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    entities = deduped["entities"]
    claims = deduped["claims"]
    evidences = deduped["evidences"]
    artifacts = deduped["artifacts"]

    entity_index = {entity["entity_id"]: entity for entity in entities}
    evidence_index = {evidence["evidence_id"]: evidence for evidence in evidences}
    artifact_index = {artifact["artifact_id"]: artifact for artifact in artifacts}

    literal_nodes: dict[str, dict[str, Any]] = {}
    graph_edges: list[dict[str, Any]] = []

    for claim in claims:
        subject_id = claim["subject_id"]
        if claim["object_type"] == "entity":
            target_id = claim["object_id"]
            target_kind = "entity"
        else:
            value = claim.get("object_value") or ""
            target_id = _literal_node_id(value)
            target_kind = "literal"
            literal_nodes[target_id] = {
                "entity_id": target_id,
                "entity_type": "literal",
                "name": value,
                "aliases": [],
                "meta": {},
            }

        graph_edges.append(
            {
                "edge_id": claim["claim_id"],
                "source": subject_id,
                "target": target_id,
                "target_kind": target_kind,
                "predicate": claim["predicate"],
                "confidence": claim["confidence"],
                "event_time": claim.get("event_time"),
                "valid_from": claim.get("valid_from"),
                "valid_to": claim.get("valid_to"),
                "is_current": claim.get("is_current", True),
                "evidence_ids": claim.get("evidence_ids", []),
            }
        )

    all_entities = entities + list(literal_nodes.values())
    entity_count_by_type = Counter(entity["entity_type"] for entity in all_entities)
    claim_count_by_predicate = Counter(claim["predicate"] for claim in claims)

    graph = {
        "meta": {
            "graph_built_at": utc_now_iso(),
            "source_corpus": corpus["meta"],
            "counts": {
                "entities": len(entities),
                "literal_nodes": len(literal_nodes),
                "claims": len(claims),
                "evidences": len(evidences),
                "artifacts": len(artifacts),
            },
            "entity_count_by_type": dict(entity_count_by_type),
            "claim_count_by_predicate": dict(claim_count_by_predicate),
            "pipeline_versions": {
                "schema_version": "1.0.0",
                "extractor_version": "spacy-rules-v1",
            },
        },
        "entities": entities,
        "claims": claims,
        "evidences": evidences,
        "artifacts": artifacts,
        "merge_log": deduped["merge_log"],
        "alias_maps": deduped["alias_maps"],
        "indices": {
            "entity_index": entity_index,
            "artifact_index": artifact_index,
            "evidence_index": evidence_index,
        },
    }

    viz_nodes: list[dict[str, Any]] = []
    for entity in all_entities:
        viz_nodes.append(
            {
                "id": entity["entity_id"],
                "label": entity["name"][:60],
                "group": entity["entity_type"],
                "title": f"{entity['entity_type']}: {entity['name']}",
            }
        )

    viz_edges: list[dict[str, Any]] = []
    for edge in graph_edges:
        evidence_preview = ""
        if edge["evidence_ids"]:
            ev = evidence_index.get(edge["evidence_ids"][0])
            if ev:
                evidence_preview = ev.get("excerpt", "")[:160]
        viz_edges.append(
            {
                "id": edge["edge_id"],
                "from": edge["source"],
                "to": edge["target"],
                "label": edge["predicate"],
                "title": f"{edge['predicate']} ({edge['confidence']:.2f})",
                "confidence": edge["confidence"],
                "event_time": edge["event_time"],
                "is_current": edge["is_current"],
                "predicate": edge["predicate"],
                "evidence_ids": edge["evidence_ids"],
                "evidence_preview": evidence_preview,
            }
        )

    viz_data = {
        "meta": graph["meta"],
        "nodes": viz_nodes,
        "edges": viz_edges,
        "claims": claims,
        "entities": entities,
        "evidences": evidences,
        "artifacts": artifacts,
        "merge_log": deduped["merge_log"],
    }
    return graph, viz_data

