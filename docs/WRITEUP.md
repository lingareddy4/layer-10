# Layer10 Take-Home Write-up

## 1) Project brief alignment

This project builds a grounded memory system over public GitHub issues data and explicitly handles:
- extraction into a typed ontology,
- evidence-first claim storage,
- multi-level deduplication/canonicalization,
- temporal correctness (`valid_from`, `valid_to`, `is_current`),
- and retrieval/visualization with auditable citations.

## 2) Public corpus and reproducibility

- Corpus type: GitHub issues + comments + issue events
- Selected corpus: `pallets/flask`
- Source endpoint family: `https://api.github.com/repos/{owner}/{repo}/issues...`
- Reproduction command:

```powershell
python scripts/run_pipeline.py --owner pallets --repo flask --max-issues 20
```

Raw downloaded snapshot is stored in:
- `outputs/raw/github_corpus_pallets_flask.json`

## 3) Ontology / extraction schema

### Entity types
- `repository`
- `issue`
- `person`
- `label`
- `component`
- `organization` (from NER)

### Claim/relationship types
- `repo_contains_issue`
- `issue_opened_by`
- `issue_status`
- `issue_assignee`
- `issue_unassigned`
- `issue_has_label`
- `issue_label_removed`
- `issue_mentions_component`
- `issue_mentions_person`
- `issue_mentions_named_entity`
- `issue_action_item`
- `issue_decision_note`
- `issue_conflict_signal`
- `issue_reproducible`

### Evidence contract (required for every claim)
Each claim stores one or more `evidence_ids` referencing evidence records containing:
- `source_id`
- `source_url`
- `artifact_id` + `artifact_type`
- exact `excerpt`
- `char_start`, `char_end` offsets
- evidence timestamp

## 4) Extraction system

### Model choice
- Free local model: `spaCy en_core_web_sm`
- Used for NER enrichment in unstructured text.

### Rule/model fusion
- Structured fields (`state`, labels, assignees, events) map to high-confidence claims.
- Unstructured issue/comment text uses sentence-level rules for:
  - action proposals,
  - decisions,
  - conflicts,
  - reproducibility signals,
  - component mentions,
  - GitHub `@mentions`.
- spaCy NER adds extra named entity links (`PERSON`, `ORG`).

### Validation and repair
- Normalize text/IDs deterministically.
- Force required fields on claims/evidence.
- Reject empty text snippets.
- Normalize aliases and deduplicate ids in-place.

### Versioning metadata
Each claim carries extraction metadata:
- `schema_version`
- `extractor_version`
- `model`

This supports backfill/reprocessing when schema or extraction logic changes.

### Quality gates
- Confidence scores per extraction channel.
- Prefer event/structured claims for stronger precision.
- Claim durability improved by evidence aggregation through dedup.

## 5) Deduplication and canonicalization

### Artifact dedup
- Exact duplicate detection via normalized text hashes.
- Near-duplicate detection (SequenceMatcher) within issue threads.
- Reversible merge log entries (`from_id -> to_id` with reason/timestamp).

### Entity canonicalization
- Type-aware normalization (especially users/components).
- Alias collapse into canonical entities.
- Reversible merge log for entity merges.

### Claim dedup
- Canonical key over `(subject, predicate, object, bucket_time for temporal predicates)`.
- Merge duplicate claims while unioning evidence sets.
- Reversible merge log for claim merges.

### Conflict/revision handling
- For exclusive predicates (`issue_status`, `issue_assignee`):
  - claims sorted by event time,
  - prior states become non-current,
  - `valid_to` is assigned when superseded.

## 6) Memory graph/store design

Serialized store: `outputs/memory_graph.json`

Core objects:
- entities
- claims (edges with temporal fields and confidence)
- evidence
- artifacts
- alias maps + merge audit logs

Update semantics:
- Pipeline is idempotent for same corpus snapshot.
- Re-run with fresh API data for incremental refresh.
- Merge logs preserve auditability and reversibility.

Observability:
- `meta.counts`
- per-predicate claim counts
- before/after dedup counts

## 7) Retrieval and grounding

Implemented in `src/layer10_memory/retrieval.py`.

Given a question:
1. Score claims with lexical overlap + confidence + recency.
2. Expand results by local graph neighborhood (same subject).
3. Return context pack with:
   - ranked claims,
   - linked entities,
   - full citations (source URL, excerpt, offsets, timestamp).

Outputs:
- `outputs/context_packs/context_pack_1.json`
- `outputs/context_packs/context_pack_2.json`
- `outputs/context_packs/context_pack_3.json`

## 8) Visualization layer

Runnables:
- `viz/index.html`
- `viz/graph_data.json`

Capabilities:
- interactive graph of entities/claims,
- filters by predicate and confidence,
- edge click opens evidence panel with source links + offsets,
- merge audit panel displays artifact/entity/claim merges.

## 9) Layer10 adaptation plan (email + Slack + Jira/Linear)

### Ontology adaptation
- Add entities: `workspace`, `channel`, `document`, `ticket`, `team`, `customer`.
- Add claims: `decision_made`, `decision_reversed`, `owns_component`, `ticket_blocks_ticket`,
  `message_refers_ticket`, `document_supersedes_document`.

### Extraction contract changes
- Require canonical source locator for each system:
  - email: message-id + mailbox/thread path
  - Slack/Teams: channel-id + ts + permalink
  - Jira/Linear: issue key + event id
- Add deletion/redaction flags per source artifact.

### Dedup/canonicalization changes
- Strong identity resolution for people across systems (email/login/display-name).
- Quote/reply stripping for chat/email to avoid duplicate memory.
- Ticket merges and renamed projects as reversible entity merges.

### Grounding and safety
- Keep immutable provenance chain from memory item to source evidence.
- If source redacted/deleted, memory claim is downgraded or masked.
- Retrieval always returns source-citable claims only.

### Long-term memory behavior
- Distinguish durable memory (decisions, ownership, persistent incidents)
  from ephemeral context (transient chatter).
- TTL/decay for low-confidence or weakly-supported claims.

### Permissions model
- Store ACL pointers per artifact and propagate to claims.
- Retrieval filters claim candidates by caller’s source access.

### Operations
- Incremental ingest by change streams/webhooks.
- Regression suite over extraction + dedup + retrieval behavior.
- Drift monitoring through precision spot checks and merge anomaly metrics.

## 10) Limitations and next improvements

- Current retrieval is lexical-first; hybrid embedding reranking would improve recall.
- No explicit human-review workflow UI yet (hooks can be added around low-confidence claims).
- Temporal conflict handling is currently focused on key exclusive predicates.
- Cross-repo dedup/entity resolution can be expanded for organization-scale memory.

