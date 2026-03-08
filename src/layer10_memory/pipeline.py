from __future__ import annotations

import argparse
import os
from pathlib import Path
from typing import Any

from .dedup import deduplicate_and_canonicalize
from .extraction import extract_structured_memory
from .github_corpus import GitHubCorpusConfig, download_github_corpus
from .graph import build_memory_graph
from .retrieval import build_context_pack
from .utils import dump_json, ensure_dir, read_json


DEFAULT_QUESTIONS = [
    "What routing-related issues were discussed, and what decisions were made?",
    "Which issues were reopened after being closed?",
    "What action items were proposed in recent bug reports?",
]


VIZ_HTML = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Layer10 Memory Graph Viewer</title>
  <script src="https://unpkg.com/vis-network/standalone/umd/vis-network.min.js"></script>
  <style>
    :root {
      --bg: #f8f7f3;
      --panel: #ffffff;
      --ink: #1d1f21;
      --muted: #63707a;
      --accent: #0b7285;
      --line: #d7dde3;
      --chip: #eef3f7;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      font-family: "Segoe UI", "IBM Plex Sans", sans-serif;
      color: var(--ink);
      background: linear-gradient(145deg, #f8f7f3 0%, #eef5f8 100%);
    }
    header {
      padding: 16px 20px;
      border-bottom: 1px solid var(--line);
      background: rgba(255,255,255,0.88);
      position: sticky;
      top: 0;
      z-index: 10;
    }
    header h1 { margin: 0; font-size: 1.1rem; }
    header p { margin: 4px 0 0; color: var(--muted); font-size: 0.9rem; }
    .layout {
      display: grid;
      grid-template-columns: minmax(300px, 1fr) 380px;
      gap: 12px;
      padding: 12px;
      min-height: calc(100vh - 74px);
    }
    #graph {
      height: calc(100vh - 110px);
      border: 1px solid var(--line);
      border-radius: 12px;
      background: var(--panel);
    }
    .side {
      display: grid;
      grid-template-rows: auto auto 1fr;
      gap: 10px;
    }
    .card {
      border: 1px solid var(--line);
      border-radius: 12px;
      background: var(--panel);
      padding: 12px;
      overflow: auto;
    }
    .controls {
      display: grid;
      grid-template-columns: 1fr 120px 150px;
      gap: 8px;
      align-items: end;
    }
    label { display: block; font-size: 0.82rem; color: var(--muted); margin-bottom: 4px; }
    select, input {
      width: 100%;
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 8px;
      font-size: 0.9rem;
      background: #fff;
    }
    .chip {
      display: inline-block;
      border-radius: 999px;
      padding: 2px 8px;
      font-size: 0.72rem;
      background: var(--chip);
      margin-right: 6px;
      margin-bottom: 4px;
    }
    .muted { color: var(--muted); }
    .evidence {
      border-top: 1px solid var(--line);
      padding-top: 8px;
      margin-top: 8px;
    }
    .merge-item {
      font-size: 0.84rem;
      border-top: 1px dashed var(--line);
      padding-top: 8px;
      margin-top: 8px;
    }
    a { color: var(--accent); text-decoration: none; }
    a:hover { text-decoration: underline; }
    @media (max-width: 980px) {
      .layout { grid-template-columns: 1fr; }
      #graph { height: 420px; }
    }
  </style>
</head>
<body>
  <header>
    <h1>Layer10 Memory Graph Viewer</h1>
    <p>Entities, claims, and grounded evidence from GitHub corpus extraction</p>
  </header>
  <section class="layout">
    <div id="graph"></div>
    <div class="side">
      <div class="card controls">
        <div>
          <label for="predicate">Predicate filter</label>
          <select id="predicate"><option value="all">All predicates</option></select>
        </div>
      <div>
        <label for="confidence">Min confidence</label>
        <input id="confidence" type="number" min="0" max="1" step="0.05" value="0" />
      </div>
      <div>
        <label for="fromDate">From date</label>
        <input id="fromDate" type="date" />
      </div>
    </div>
      <div class="card" id="selection">
        <strong>Selection</strong>
        <p class="muted">Click a claim edge to inspect supporting evidence and metadata.</p>
      </div>
      <div class="card" id="merges">
        <strong>Merge Audit</strong>
        <p class="muted">Artifact/entity/claim merges are reversible and logged here.</p>
      </div>
    </div>
  </section>
  <script>
    const predicateEl = document.getElementById("predicate");
    const confidenceEl = document.getElementById("confidence");
    const fromDateEl = document.getElementById("fromDate");
    const selectionEl = document.getElementById("selection");
    const mergesEl = document.getElementById("merges");
    let rawData = null;
    let network = null;

    function byId(items) {
      const map = new Map();
      for (const item of items) map.set(item.evidence_id || item.entity_id || item.id, item);
      return map;
    }

    function renderNetwork() {
      const minConfidence = Number(confidenceEl.value || "0");
      const predicate = predicateEl.value;
      const fromDate = fromDateEl.value ? new Date(fromDateEl.value + "T00:00:00Z") : null;
      const edges = rawData.edges.filter((edge) => {
        if (edge.confidence < minConfidence) return false;
        if (predicate !== "all" && edge.predicate !== predicate) return false;
        if (fromDate && edge.event_time) {
          const edgeDate = new Date(edge.event_time);
          if (edgeDate < fromDate) return false;
        }
        return true;
      });
      const activeNodeIds = new Set();
      for (const edge of edges) {
        activeNodeIds.add(edge.from);
        activeNodeIds.add(edge.to);
      }
      const nodes = rawData.nodes.filter((node) => activeNodeIds.has(node.id));

      const container = document.getElementById("graph");
      const data = {
        nodes: new vis.DataSet(nodes),
        edges: new vis.DataSet(edges.map((edge) => ({
          ...edge,
          arrows: "to",
        }))),
      };
      const options = {
        interaction: { hover: true },
        physics: { stabilization: false, barnesHut: { springLength: 160 } },
        nodes: { shape: "dot", size: 14, font: { size: 13 } },
        edges: { color: "#7f8f9c", smooth: { type: "dynamic" }, font: { size: 10, align: "middle" } },
        groups: {
          issue: { color: "#0b7285" },
          person: { color: "#c2255c" },
          repository: { color: "#2b8a3e" },
          label: { color: "#d9480f" },
          component: { color: "#5f3dc4" },
          literal: { color: "#5c6770" },
          organization: { color: "#6741d9" }
        },
      };
      network = new vis.Network(container, data, options);
      network.on("selectEdge", (params) => {
        if (!params.edges.length) return;
        const edgeId = params.edges[0];
        const edge = edges.find((item) => item.id === edgeId);
        if (!edge) return;
        const evidenceMap = byId(rawData.evidences);
        const evidenceHtml = (edge.evidence_ids || []).map((evId) => {
          const ev = evidenceMap.get(evId);
          if (!ev) return "";
          const source = ev.source_url ? `<a href="${ev.source_url}" target="_blank" rel="noreferrer">open source</a>` : "";
          return `
            <div class="evidence">
              <div class="chip">${ev.artifact_type || "artifact"}</div>
              <span class="chip">offset ${ev.char_start}-${ev.char_end}</span>
              <div>${ev.excerpt || ""}</div>
              <div class="muted">${ev.timestamp || ""} ${source}</div>
            </div>
          `;
        }).join("");
        selectionEl.innerHTML = `
          <strong>${edge.predicate}</strong>
          <div class="muted">confidence=${Number(edge.confidence).toFixed(3)} | current=${edge.is_current}</div>
          <div class="muted">event_time=${edge.event_time || "n/a"}</div>
          ${evidenceHtml || "<p class='muted'>No evidence available</p>"}
        `;
      });
    }

    function renderPredicateOptions() {
      const predicates = Array.from(new Set(rawData.edges.map((edge) => edge.predicate))).sort();
      for (const predicate of predicates) {
        const option = document.createElement("option");
        option.value = predicate;
        option.textContent = predicate;
        predicateEl.appendChild(option);
      }
    }

    function renderMerges() {
      const mergeGroups = rawData.merge_log || {};
      const blocks = [];
      for (const [name, items] of Object.entries(mergeGroups)) {
        blocks.push(`<div class="chip">${name}: ${items.length}</div>`);
        for (const item of items.slice(0, 40)) {
          blocks.push(`
            <div class="merge-item">
              <div><strong>${item.merge_type}</strong></div>
              <div class="muted">${item.from_id} -> ${item.to_id}</div>
              <div>${item.reason || ""}</div>
            </div>
          `);
        }
      }
      mergesEl.innerHTML = `<strong>Merge Audit</strong>${blocks.join("") || "<p class='muted'>No merges logged.</p>"}`;
    }

    async function boot() {
      const response = await fetch("graph_data.json");
      rawData = await response.json();
      renderPredicateOptions();
      renderMerges();
      renderNetwork();
      predicateEl.addEventListener("change", renderNetwork);
      confidenceEl.addEventListener("input", renderNetwork);
      fromDateEl.addEventListener("change", renderNetwork);
    }

    boot().catch((err) => {
      selectionEl.innerHTML = `<strong>Error</strong><p class="muted">${err.message}</p>`;
    });
  </script>
</body>
</html>
"""


def _load_questions(path: Path | None) -> list[str]:
    if not path:
        return DEFAULT_QUESTIONS
    lines = [line.strip() for line in path.read_text(encoding="utf-8").splitlines()]
    return [line for line in lines if line and not line.startswith("#")]


def run_pipeline(args: argparse.Namespace) -> dict[str, Any]:
    output_dir = Path(args.output_dir).resolve()
    raw_dir = output_dir / "raw"
    context_dir = output_dir / "context_packs"
    viz_dir = Path(args.viz_dir).resolve()
    ensure_dir(raw_dir)
    ensure_dir(context_dir)
    ensure_dir(viz_dir)

    corpus_path = raw_dir / f"github_corpus_{args.owner}_{args.repo}.json"
    if args.use_existing_corpus and corpus_path.exists():
        corpus = read_json(corpus_path)
    else:
        corpus = download_github_corpus(
            GitHubCorpusConfig(
                owner=args.owner,
                repo=args.repo,
                max_issues=args.max_issues,
                state=args.issue_state,
                token=args.github_token,
            )
        )
        dump_json(corpus_path, corpus)

    extraction = extract_structured_memory(corpus)
    dump_json(raw_dir / "extraction_output.json", extraction)

    deduped = deduplicate_and_canonicalize(corpus, extraction)
    dump_json(raw_dir / "dedup_output.json", deduped)

    graph, viz_data = build_memory_graph(corpus, deduped)
    graph_for_disk = graph.copy()
    graph_for_disk.pop("indices", None)
    dump_json(output_dir / "memory_graph.json", graph_for_disk)
    dump_json(viz_dir / "graph_data.json", viz_data)
    (viz_dir / "index.html").write_text(VIZ_HTML, encoding="utf-8")

    questions = _load_questions(Path(args.questions_file).resolve() if args.questions_file else None)
    packs: list[dict[str, Any]] = []
    for idx, question in enumerate(questions, start=1):
        pack = build_context_pack(graph_for_disk, question=question, top_k=args.top_k)
        packs.append(pack)
        dump_json(context_dir / f"context_pack_{idx}.json", pack)

    summary = {
        "corpus_path": str(corpus_path),
        "memory_graph_path": str(output_dir / "memory_graph.json"),
        "viz_index_path": str(viz_dir / "index.html"),
        "questions": questions,
        "context_pack_count": len(packs),
        "counts": graph_for_disk["meta"]["counts"],
    }
    dump_json(output_dir / "run_summary.json", summary)
    return summary


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run Layer10 take-home memory graph pipeline.")
    parser.add_argument("--owner", default="pallets")
    parser.add_argument("--repo", default="flask")
    parser.add_argument("--max-issues", type=int, default=20)
    parser.add_argument("--issue-state", default="all", choices=["all", "open", "closed"])
    parser.add_argument("--github-token", default=os.getenv("GITHUB_TOKEN"))
    parser.add_argument("--output-dir", default="outputs")
    parser.add_argument("--viz-dir", default="viz")
    parser.add_argument("--questions-file")
    parser.add_argument("--top-k", type=int, default=8)
    parser.add_argument("--use-existing-corpus", action="store_true")
    return parser


def main() -> None:
    parser = build_arg_parser()
    args = parser.parse_args()
    summary = run_pipeline(args)
    print("Pipeline complete.")
    for key, value in summary.items():
        print(f"- {key}: {value}")


if __name__ == "__main__":
    main()
