"""Join a support_eval report with human correctness labels, then score accuracy.

Labels JSONL: one {"id": "...", "answer_correct": true|false} per case.
Review the answer against the relevant published source and game version.
"""

import argparse
import hashlib
import json
from pathlib import Path


def load_labels(path: Path) -> dict[str, bool]:
    """Require one explicit boolean judgment for every labeled case."""
    labels: dict[str, bool] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        item = json.loads(line)
        case_id = item.get("id")
        judgment = item.get("answer_correct")
        if not isinstance(case_id, str) or not case_id or case_id in labels:
            raise ValueError("Labels need unique nonempty case IDs")
        if not isinstance(judgment, bool):
            raise ValueError(f"{case_id}: answer_correct must be true or false")
        labels[case_id] = judgment
    return labels


def main() -> None:
    """Reject partial labeling and save a report tied to the source run."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, required=True, help="support_eval report JSON")
    parser.add_argument("--labels", type=Path, required=True, help="Human labels JSONL")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--expected-count", type=int, default=None)
    args = parser.parse_args()
    report = json.loads(args.run.read_text(encoding="utf-8"))
    rows = report["cases"]
    if not rows or (args.expected_count is not None and len(rows) != args.expected_count):
        raise ValueError("Run case count differs from the expected count")
    ids = [row["id"] for row in rows]
    if len(set(ids)) != len(ids):
        raise ValueError("Run contains duplicate case IDs")
    labels = load_labels(args.labels)
    if set(ids) != set(labels):
        raise ValueError(f"Missing labels: {sorted(set(ids) - set(labels))}; extra labels: {sorted(set(labels) - set(ids))}")
    result = {
        "run_report": str(args.run),
        "dataset_sha256": report["dataset_sha256"],
        "labels_sha256": hashlib.sha256(args.labels.read_bytes()).hexdigest(),
        "cases": len(rows),
        "metrics": {
            "answer_correct_count": sum(labels[case_id] for case_id in ids),
            "answer_accuracy": sum(labels[case_id] for case_id in ids) / len(rows),
            "handoff_accuracy": sum(row["handoff_ok"] for row in rows) / len(rows),
        },
        "rows": [
            {"id": row["id"], "answer_correct": labels[row["id"]], "handoff_ok": row["handoff_ok"]}
            for row in rows
        ],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result["metrics"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
