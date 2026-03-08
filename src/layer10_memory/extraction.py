from __future__ import annotations

import re
from typing import Any

from .utils import normalize_space, sentence_spans, stable_hash, utc_now_iso

SCHEMA_VERSION = "1.0.0"
EXTRACTOR_VERSION = "spacy-rules-v1"
MODEL_NAME = "en_core_web_sm"

MENTION_RE = re.compile(r"@([A-Za-z0-9_-]+)")

COMPONENT_RULES = {
    "routing": ["route", "routing", "url_map", "url map", "werkzeug"],
    "templating": ["jinja", "template", "render_template"],
    "cli": ["cli", "command line", "flask run", "click"],
    "documentation": ["docs", "documentation", "readme"],
    "testing": ["pytest", "unit test", "integration test", "failing test"],
    "sessions": ["session", "cookie", "secure cookie"],
    "security": ["csrf", "xss", "security", "vulnerability"],
    "async": ["async", "await", "asgi"],
    "database": ["sqlalchemy", "database", "db", "sqlite", "postgres"],
}

PROPOSAL_MARKERS = [
    "should",
    "need to",
    "could",
    "would be better",
    "recommend",
    "proposal",
    "let's",
]
DECISION_MARKERS = [
    "decided",
    "decision",
    "resolved",
    "we will",
    "we won't",
    "closing as",
    "accepted",
    "rejected",
]
CONFLICT_MARKERS = [
    "however",
    "on the other hand",
    "disagree",
    "contradict",
    "conflict",
    "not sure",
]
REPRO_MARKERS = [
    "can reproduce",
    "reproduced",
    "confirmed",
    "steps to reproduce",
    "fails on",
]


def _load_nlp():
    try:
        import spacy

        return spacy.load(MODEL_NAME)
    except Exception:
        return None


def _make_evidence(
    evidences: list[dict[str, Any]],
    artifact: dict[str, Any],
    excerpt: str,
    char_start: int,
    char_end: int,
) -> str:
    excerpt = normalize_space(excerpt)
    evidence_id = "ev_" + stable_hash(
        [
            artifact["artifact_id"],
            str(char_start),
            str(char_end),
            excerpt,
        ],
        length=20,
    )
    evidences.append(
        {
            "evidence_id": evidence_id,
            "artifact_id": artifact["artifact_id"],
            "source_id": artifact.get("source_id"),
            "source_url": artifact.get("source_url"),
            "artifact_type": artifact.get("artifact_type"),
            "excerpt": excerpt,
            "char_start": int(char_start),
            "char_end": int(char_end),
            "timestamp": artifact.get("created_at"),
        }
    )
    return evidence_id


def _entity(
    entities: dict[str, dict[str, Any]],
    entity_id: str,
    entity_type: str,
    name: str,
    alias: str | None = None,
    meta: dict[str, Any] | None = None,
) -> None:
    existing = entities.get(entity_id)
    if existing:
        if alias and alias not in existing["aliases"]:
            existing["aliases"].append(alias)
        return
    entities[entity_id] = {
        "entity_id": entity_id,
        "entity_type": entity_type,
        "name": name,
        "aliases": [alias] if alias else [],
        "meta": meta or {},
    }


def _claim(
    claims: list[dict[str, Any]],
    subject_id: str,
    predicate: str,
    object_type: str,
    confidence: float,
    evidence_id: str,
    event_time: str | None,
    source_artifact_id: str,
    object_id: str | None = None,
    object_value: str | None = None,
) -> None:
    obj_key = object_id if object_type == "entity" else (object_value or "")
    claim_id = "clm_" + stable_hash(
        [subject_id, predicate, object_type, obj_key, source_artifact_id, evidence_id], length=20
    )
    claims.append(
        {
            "claim_id": claim_id,
            "subject_id": subject_id,
            "predicate": predicate,
            "object_type": object_type,
            "object_id": object_id,
            "object_value": object_value,
            "confidence": round(float(confidence), 4),
            "evidence_ids": [evidence_id],
            "source_artifact_id": source_artifact_id,
            "event_time": event_time,
            "valid_from": event_time,
            "valid_to": None,
            "is_current": True,
            "extraction": {
                "schema_version": SCHEMA_VERSION,
                "extractor_version": EXTRACTOR_VERSION,
                "model": MODEL_NAME,
            },
        }
    )


def _component_matches(sentence_lc: str) -> list[str]:
    hits: list[str] = []
    for component, terms in COMPONENT_RULES.items():
        if any(term in sentence_lc for term in terms):
            hits.append(component)
    return hits


def _extract_text_claims(
    nlp,
    entities: dict[str, dict[str, Any]],
    claims: list[dict[str, Any]],
    evidences: list[dict[str, Any]],
    artifact: dict[str, Any],
    issue_entity_id: str,
) -> None:
    text = artifact.get("text") or ""
    if not text:
        return

    for start, end, sentence in sentence_spans(text):
        sentence_lc = sentence.lower()
        ev_id = _make_evidence(evidences, artifact, sentence, start, end)

        for component in _component_matches(sentence_lc):
            component_id = f"component:{component}"
            _entity(
                entities,
                component_id,
                "component",
                component.replace("_", " ").title(),
                alias=component,
            )
            _claim(
                claims,
                subject_id=issue_entity_id,
                predicate="issue_mentions_component",
                object_type="entity",
                object_id=component_id,
                confidence=0.72,
                evidence_id=ev_id,
                event_time=artifact.get("created_at"),
                source_artifact_id=artifact["artifact_id"],
            )

        if any(marker in sentence_lc for marker in REPRO_MARKERS):
            _claim(
                claims,
                subject_id=issue_entity_id,
                predicate="issue_reproducible",
                object_type="literal",
                object_value="true",
                confidence=0.78,
                evidence_id=ev_id,
                event_time=artifact.get("created_at"),
                source_artifact_id=artifact["artifact_id"],
            )

        if any(marker in sentence_lc for marker in DECISION_MARKERS):
            _claim(
                claims,
                subject_id=issue_entity_id,
                predicate="issue_decision_note",
                object_type="literal",
                object_value=sentence[:280],
                confidence=0.69,
                evidence_id=ev_id,
                event_time=artifact.get("created_at"),
                source_artifact_id=artifact["artifact_id"],
            )

        if any(marker in sentence_lc for marker in PROPOSAL_MARKERS):
            _claim(
                claims,
                subject_id=issue_entity_id,
                predicate="issue_action_item",
                object_type="literal",
                object_value=sentence[:280],
                confidence=0.65,
                evidence_id=ev_id,
                event_time=artifact.get("created_at"),
                source_artifact_id=artifact["artifact_id"],
            )

        if any(marker in sentence_lc for marker in CONFLICT_MARKERS):
            _claim(
                claims,
                subject_id=issue_entity_id,
                predicate="issue_conflict_signal",
                object_type="literal",
                object_value=sentence[:280],
                confidence=0.6,
                evidence_id=ev_id,
                event_time=artifact.get("created_at"),
                source_artifact_id=artifact["artifact_id"],
            )

        for mention in MENTION_RE.findall(sentence):
            user_id = f"user:{mention.lower()}"
            _entity(entities, user_id, "person", mention, alias=f"@{mention}")
            _claim(
                claims,
                subject_id=issue_entity_id,
                predicate="issue_mentions_person",
                object_type="entity",
                object_id=user_id,
                confidence=0.91,
                evidence_id=ev_id,
                event_time=artifact.get("created_at"),
                source_artifact_id=artifact["artifact_id"],
            )

    if nlp is None:
        return

    doc = nlp(text[:20000])
    for ent in doc.ents:
        if ent.label_ not in {"PERSON", "ORG"}:
            continue
        start = int(ent.start_char)
        end = int(ent.end_char)
        span = text[start:end]
        ev_id = _make_evidence(evidences, artifact, span, start, end)
        entity_name = normalize_space(span)
        if not entity_name:
            continue
        entity_id = f"named:{entity_name.lower().replace(' ', '_')}"
        entity_type = "person" if ent.label_ == "PERSON" else "organization"
        _entity(entities, entity_id, entity_type, entity_name)
        _claim(
            claims,
            subject_id=issue_entity_id,
            predicate="issue_mentions_named_entity",
            object_type="entity",
            object_id=entity_id,
            confidence=0.55,
            evidence_id=ev_id,
            event_time=artifact.get("created_at"),
            source_artifact_id=artifact["artifact_id"],
        )


def _event_claims(
    entities: dict[str, dict[str, Any]],
    claims: list[dict[str, Any]],
    evidences: list[dict[str, Any]],
    artifact: dict[str, Any],
    issue_entity_id: str,
) -> None:
    event_type = (artifact.get("meta") or {}).get("event")
    if not event_type:
        return
    text = artifact.get("text") or f"event={event_type}"
    ev_id = _make_evidence(evidences, artifact, text, 0, len(text))

    if event_type == "closed":
        _claim(
            claims,
            subject_id=issue_entity_id,
            predicate="issue_status",
            object_type="literal",
            object_value="closed",
            confidence=0.99,
            evidence_id=ev_id,
            event_time=artifact.get("created_at"),
            source_artifact_id=artifact["artifact_id"],
        )
    elif event_type == "reopened":
        _claim(
            claims,
            subject_id=issue_entity_id,
            predicate="issue_status",
            object_type="literal",
            object_value="open",
            confidence=0.99,
            evidence_id=ev_id,
            event_time=artifact.get("created_at"),
            source_artifact_id=artifact["artifact_id"],
        )
    elif event_type in {"assigned", "unassigned"}:
        assignee = (artifact.get("meta") or {}).get("assignee")
        if assignee:
            user_id = f"user:{assignee.lower()}"
            _entity(entities, user_id, "person", assignee, alias=f"@{assignee}")
            predicate = "issue_assignee" if event_type == "assigned" else "issue_unassigned"
            _claim(
                claims,
                subject_id=issue_entity_id,
                predicate=predicate,
                object_type="entity",
                object_id=user_id,
                confidence=0.97,
                evidence_id=ev_id,
                event_time=artifact.get("created_at"),
                source_artifact_id=artifact["artifact_id"],
            )
    elif event_type in {"labeled", "unlabeled"}:
        label = (artifact.get("meta") or {}).get("label")
        if label:
            label_id = f"label:{label.lower()}"
            _entity(entities, label_id, "label", label)
            predicate = "issue_has_label" if event_type == "labeled" else "issue_label_removed"
            _claim(
                claims,
                subject_id=issue_entity_id,
                predicate=predicate,
                object_type="entity",
                object_id=label_id,
                confidence=0.97,
                evidence_id=ev_id,
                event_time=artifact.get("created_at"),
                source_artifact_id=artifact["artifact_id"],
            )


def extract_structured_memory(corpus: dict[str, Any]) -> dict[str, Any]:
    nlp = _load_nlp()
    entities: dict[str, dict[str, Any]] = {}
    claims: list[dict[str, Any]] = []
    evidences: list[dict[str, Any]] = []

    owner = corpus["meta"]["owner"]
    repo = corpus["meta"]["repo"]
    repo_id = f"repo:{owner.lower()}/{repo.lower()}"
    _entity(
        entities,
        repo_id,
        "repository",
        f"{owner}/{repo}",
        meta={"owner": owner, "repo": repo},
    )

    issue_artifacts_by_number: dict[int, dict[str, Any]] = {}
    for artifact in corpus.get("artifacts", []):
        if artifact.get("artifact_type") == "issue_body":
            issue_artifacts_by_number[int(artifact["issue_number"])] = artifact

    for issue in corpus.get("issues", []):
        number = int(issue["number"])
        issue_entity_id = f"issue:{owner.lower()}/{repo.lower()}#{number}"
        _entity(
            entities,
            issue_entity_id,
            "issue",
            f"{owner}/{repo}#{number}",
            meta={
                "number": number,
                "title": issue.get("title"),
                "url": issue.get("html_url"),
            },
        )

        # repository -> issue link
        issue_artifact = issue_artifacts_by_number.get(number)
        if issue_artifact:
            ev_id = _make_evidence(
                evidences,
                issue_artifact,
                issue_artifact.get("text", "")[:220],
                0,
                min(len(issue_artifact.get("text", "")), 220),
            )
            _claim(
                claims,
                subject_id=repo_id,
                predicate="repo_contains_issue",
                object_type="entity",
                object_id=issue_entity_id,
                confidence=1.0,
                evidence_id=ev_id,
                event_time=issue.get("created_at"),
                source_artifact_id=issue_artifact["artifact_id"],
            )

        creator = issue.get("user")
        if creator:
            creator_id = f"user:{creator.lower()}"
            _entity(entities, creator_id, "person", creator, alias=f"@{creator}")
            if issue_artifact:
                ev_id = _make_evidence(
                    evidences,
                    issue_artifact,
                    f"Opened by @{creator}",
                    0,
                    min(len(issue_artifact.get("text", "")), 1),
                )
                _claim(
                    claims,
                    subject_id=issue_entity_id,
                    predicate="issue_opened_by",
                    object_type="entity",
                    object_id=creator_id,
                    confidence=0.99,
                    evidence_id=ev_id,
                    event_time=issue.get("created_at"),
                    source_artifact_id=issue_artifact["artifact_id"],
                )

        if issue_artifact:
            ev_id = _make_evidence(
                evidences,
                issue_artifact,
                f"state={issue.get('state')}",
                0,
                min(len(issue_artifact.get("text", "")), 1),
            )
            _claim(
                claims,
                subject_id=issue_entity_id,
                predicate="issue_status",
                object_type="literal",
                object_value=issue.get("state", "open"),
                confidence=0.97,
                evidence_id=ev_id,
                event_time=issue.get("updated_at") or issue.get("created_at"),
                source_artifact_id=issue_artifact["artifact_id"],
            )

        for label in issue.get("labels", []):
            label_name = label.get("name")
            if not label_name or not issue_artifact:
                continue
            label_id = f"label:{label_name.lower()}"
            _entity(entities, label_id, "label", label_name)
            ev_id = _make_evidence(
                evidences,
                issue_artifact,
                f"label={label_name}",
                0,
                min(len(issue_artifact.get("text", "")), 1),
            )
            _claim(
                claims,
                subject_id=issue_entity_id,
                predicate="issue_has_label",
                object_type="entity",
                object_id=label_id,
                confidence=0.98,
                evidence_id=ev_id,
                event_time=issue.get("updated_at") or issue.get("created_at"),
                source_artifact_id=issue_artifact["artifact_id"],
            )

        for assignee in issue.get("assignees", []):
            if not assignee or not issue_artifact:
                continue
            user_id = f"user:{assignee.lower()}"
            _entity(entities, user_id, "person", assignee, alias=f"@{assignee}")
            ev_id = _make_evidence(
                evidences,
                issue_artifact,
                f"assignee=@{assignee}",
                0,
                min(len(issue_artifact.get("text", "")), 1),
            )
            _claim(
                claims,
                subject_id=issue_entity_id,
                predicate="issue_assignee",
                object_type="entity",
                object_id=user_id,
                confidence=0.98,
                evidence_id=ev_id,
                event_time=issue.get("updated_at") or issue.get("created_at"),
                source_artifact_id=issue_artifact["artifact_id"],
            )

    for artifact in corpus.get("artifacts", []):
        number = int(artifact.get("issue_number"))
        issue_entity_id = f"issue:{owner.lower()}/{repo.lower()}#{number}"
        artifact_type = artifact.get("artifact_type")
        if artifact_type in {"issue_body", "comment"}:
            _extract_text_claims(nlp, entities, claims, evidences, artifact, issue_entity_id)
        elif artifact_type == "event":
            _event_claims(entities, claims, evidences, artifact, issue_entity_id)

    return {
        "meta": {
            "schema_version": SCHEMA_VERSION,
            "extractor_version": EXTRACTOR_VERSION,
            "model": MODEL_NAME if nlp else "rule_only_fallback",
            "extracted_at": utc_now_iso(),
            "entity_count": len(entities),
            "claim_count": len(claims),
            "evidence_count": len(evidences),
        },
        "entities": list(entities.values()),
        "claims": claims,
        "evidences": evidences,
    }

