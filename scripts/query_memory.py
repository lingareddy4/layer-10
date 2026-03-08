from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from layer10_memory.retrieval import build_context_pack  # noqa: E402
from layer10_memory.utils import dump_json, read_json  # noqa: E402


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Query the memory graph and produce a context pack.")
    parser.add_argument("--graph", default="outputs/memory_graph.json")
    parser.add_argument("--question", required=True)
    parser.add_argument("--top-k", type=int, default=8)
    parser.add_argument("--out")
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    graph = read_json(Path(args.graph).resolve())
    pack = build_context_pack(graph, question=args.question, top_k=args.top_k)

    if args.out:
        dump_json(Path(args.out).resolve(), pack)
        print(f"Wrote context pack to {Path(args.out).resolve()}")
    else:
        print(json.dumps(pack, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()

