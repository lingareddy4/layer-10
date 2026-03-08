# Layer10 Take-Home (2026)

Grounded long-term memory pipeline over a **GitHub Issues corpus**, including:
- structured extraction with evidence grounding,
- multi-level dedup/canonicalization,
- graph construction with temporal claim handling,
- grounded retrieval context packs,
- and an explorable visualization layer.

## Corpus used
- Repository: `pallets/flask`
- Source: GitHub REST API (`issues`, `comments`, `events`)
- Download is reproducible via:

```powershell
python scripts/run_pipeline.py --owner pallets --repo flask --max-issues 20
```

Optional: set `GITHUB_TOKEN` to avoid low unauthenticated rate limits.

## Setup

```powershell
python -m pip install -r requirements.txt
python -m spacy download en_core_web_sm
```

## Run end-to-end

```powershell
python scripts/run_pipeline.py --owner pallets --repo flask --max-issues 20
```

## Output artifacts

- `outputs/raw/github_corpus_pallets_flask.json`: downloaded corpus snapshot
- `outputs/raw/extraction_output.json`: extracted entities/claims/evidence
- `outputs/raw/dedup_output.json`: canonicalized memory + merge logs
- `outputs/memory_graph.json`: serialized memory graph/store
- `outputs/context_packs/context_pack_*.json`: grounded retrieval examples
- `viz/graph_data.json`: visualization graph payload
- `viz/index.html`: runnable interactive graph viewer
- `docs/WRITEUP.md`: design and Layer10 adaptation details

## Visualization

From repo root:

```powershell
python -m http.server 8000
```

Open:
- `http://localhost:8000/viz/index.html`

Viewer supports:
- graph navigation,
- predicate/confidence filters,
- evidence panel with source links + offsets,
- merge-audit panel for dedup/canonicalization actions.

## Retrieval examples

Generated questions:
- “What routing-related issues were discussed, and what decisions were made?”
- “Which issues were reopened after being closed?”
- “What action items were proposed in recent bug reports?”

Each context pack includes ranked claims, linked entities, and explicit citations.

Run ad-hoc query:

```powershell
python scripts/query_memory.py --graph outputs/memory_graph.json --question "Which issues were reopened after being closed?"
```
