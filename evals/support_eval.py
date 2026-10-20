"""Run synthetic multilingual support regression cases against the current agent.

Usage: uv run python -m evals.support_eval --output support-eval.json
The application database and model provider must be configured before running.
"""

import argparse
import asyncio
import hashlib
import json
import selectors
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel
from rich.console import Console
from rich.table import Table

from app.core.config import settings
from app.services.game_support_agent import SupportState, answer_player, classify_handoff

CASES_PATH = Path(__file__).with_name("support_cases.jsonl")


class SupportCase(BaseModel):
    """Human-reviewed expected behavior for one synthetic query."""

    id: str
    language: str
    question: str
    expect_handoff: bool
    expect_citation: bool
    expected_source_title: str | None = None


def load_cases(path: Path = CASES_PATH) -> list[SupportCase]:
    """Reject malformed or duplicate dataset entries before calling the model."""
    cases = [SupportCase.model_validate_json(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if len({case.id for case in cases}) != len(cases):
        raise ValueError("Duplicate support case ID")
    return cases


def score_case(case: SupportCase, answer: SupportState, elapsed_ms: float) -> dict[str, Any]:
    """Score observable citation presence and handoff routing."""
    sources = answer.get("sources") or []
    actual_handoff = classify_handoff(case.question, answer) is not None
    citation_ok = (any(case.expected_source_title.casefold() in source.title.casefold() for source in sources)
                   if case.expected_source_title else bool(sources)) if case.expect_citation else True
    return {
        "id": case.id, "language": case.language, "elapsed_ms": round(elapsed_ms, 1),
        "handoff_expected": case.expect_handoff, "handoff_actual": actual_handoff,
        "handoff_ok": actual_handoff == case.expect_handoff,
        "citation_expected": case.expect_citation, "expected_source_title": case.expected_source_title,
        "citation_count": len(sources),
        "citation_ok": citation_ok,
        "source_urls": [str(source.url) for source in sources],
        "answer": answer.get("answer", ""),
    }


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Summarize missed and needless handoffs, citation coverage, and latency."""
    count = len(rows)
    cited = [row for row in rows if row["citation_expected"]]
    return {
        "cases": count,
        "handoff_accuracy": sum(row["handoff_ok"] for row in rows) / count if count else 0,
        "missed_handoffs": sum(row["handoff_expected"] and not row["handoff_actual"] for row in rows),
        "unnecessary_handoffs": sum(not row["handoff_expected"] and row["handoff_actual"] for row in rows),
        "citation_coverage": sum(row["citation_ok"] for row in cited) / len(cited) if cited else 0,
        "latency_p50_ms": round(statistics.median(row["elapsed_ms"] for row in rows), 1) if rows else 0,
        "latency_p95_ms": round(sorted(row["elapsed_ms"] for row in rows)[max(0, int(count * 0.95) - 1)], 1) if rows else 0,
    }


async def evaluate(cases: list[SupportCase]) -> list[dict[str, Any]]:
    """Use the production graph with isolated synthetic identities."""
    rows = []
    for case in cases:
        started = time.perf_counter()
        answer = await answer_player(case.question, case.language, [], "")
        rows.append(score_case(case, answer, (time.perf_counter() - started) * 1000))
    return rows


def main() -> None:
    """Write a versioned JSON report and show key metrics in the console."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("support-eval.json"))
    parser.add_argument("--baseline", type=Path, default=None)
    args = parser.parse_args()
    if sys.platform == "win32":
        rows = asyncio.run(evaluate(load_cases()),
                           loop_factory=lambda: asyncio.SelectorEventLoop(selectors.SelectSelector()))
    else:
        rows = asyncio.run(evaluate(load_cases()))
    metrics = summarize(rows)
    report = {"created_at": datetime.now(timezone.utc).isoformat(), "model": settings.DEFAULT_LLM_MODEL,
              "dataset": "evals/support_cases.jsonl",
              "dataset_sha256": hashlib.sha256(CASES_PATH.read_bytes()).hexdigest(),
              "metrics": metrics, "cases": rows}
    if args.baseline:
        previous = json.loads(args.baseline.read_text(encoding="utf-8"))["metrics"]
        report["delta_vs_baseline"] = {key: round(value - previous[key], 3) for key, value in metrics.items()
                                       if isinstance(value, (int, float)) and key in previous}
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    table = Table(title="DOTS Survivors support evaluation")
    table.add_column("Metric")
    table.add_column("Value", justify="right")
    for key, value in metrics.items():
        table.add_row(key, str(value))
    Console().print(table)


if __name__ == "__main__":
    main()
