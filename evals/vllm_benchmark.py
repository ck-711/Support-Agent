"""Benchmark a running OpenAI-compatible vLLM chat endpoint with SSE streaming.

Usage: uv run python -m evals.vllm_benchmark --base-url http://127.0.0.1:8000/v1 \
    --model MODEL --input evals/support_cases.jsonl --output vllm-benchmark.json
This benchmarks the model endpoint alone, not the complete customer-service graph.
"""

import argparse
import asyncio
import hashlib
import json
import math
import os
import statistics
import time
from pathlib import Path
from typing import Any

import httpx


def p95(values: list[float]) -> float:
    """Return linearly interpolated 95th percentile."""
    ordered = sorted(values)
    rank = (len(ordered) - 1) * 0.95
    low = math.floor(rank)
    high = math.ceil(rank)
    return ordered[low] + (ordered[high] - ordered[low]) * (rank - low)


async def run_case(
    client: httpx.AsyncClient, semaphore: asyncio.Semaphore, base_url: str,
    model: str, case: dict[str, str], api_key: str, max_tokens: int,
) -> dict[str, Any]:
    """Read one stream and measure first content token and final completion."""
    async with semaphore:
        started = time.perf_counter()
        first_token_ms = None
        output_tokens = None
        content = []
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        request = {
            "model": model, "messages": [{"role": "user", "content": case["question"]}],
            "max_tokens": max_tokens, "temperature": 0, "stream": True,
            "stream_options": {"include_usage": True},
        }
        async with client.stream("POST", f"{base_url.rstrip('/')}/chat/completions", json=request, headers=headers) as response:
            response.raise_for_status()
            async for line in response.aiter_lines():
                if not line.startswith("data:"):
                    continue
                payload = line[5:].strip()
                if payload == "[DONE]":
                    continue
                event = json.loads(payload)
                usage = event.get("usage")
                if usage and isinstance(usage.get("completion_tokens"), int):
                    output_tokens = usage["completion_tokens"]
                for choice in event.get("choices") or []:
                    token_text = (choice.get("delta") or {}).get("content")
                    if token_text:
                        if first_token_ms is None:
                            first_token_ms = (time.perf_counter() - started) * 1000
                        content.append(token_text)
        elapsed = time.perf_counter() - started
        if first_token_ms is None:
            raise ValueError(f"{case['id']}: model returned no content token")
        return {
            "id": case["id"], "ttft_ms": round(first_token_ms, 2),
            "elapsed_ms": round(elapsed * 1000, 2), "output_tokens": output_tokens,
            "output_characters": len("".join(content)),
        }


async def benchmark(args: argparse.Namespace, cases: list[dict[str, str]]) -> dict[str, Any]:
    """Run fixed concurrency without silently dropping failed requests."""
    timeout = httpx.Timeout(args.timeout)
    limits = httpx.Limits(max_connections=args.concurrency)
    async with httpx.AsyncClient(timeout=timeout, limits=limits) as client:
        semaphore = asyncio.Semaphore(args.concurrency)
        wall_started = time.perf_counter()
        rows = await asyncio.gather(*(
            run_case(client, semaphore, args.base_url, args.model, case, args.api_key, args.max_tokens)
            for case in cases
        ))
        wall_seconds = time.perf_counter() - wall_started
    token_counts = [row["output_tokens"] for row in rows]
    return {
        "model": args.model, "base_url": args.base_url, "concurrency": args.concurrency,
        "hardware": args.hardware, "vllm_version": args.vllm_version,
        "max_output_tokens": args.max_tokens, "cases": len(rows),
        "dataset_sha256": hashlib.sha256(args.input.read_bytes()).hexdigest(),
        "metrics": {
            "ttft_p50_ms": round(statistics.median(row["ttft_ms"] for row in rows), 2),
            "ttft_p95_ms": round(p95([row["ttft_ms"] for row in rows]), 2),
            "wall_seconds": round(wall_seconds, 2),
            "aggregate_output_tokens_per_second": (
                round(sum(token_counts) / wall_seconds, 2)
                if all(count is not None for count in token_counts) else None
            ),
            "requests_with_usage": sum(count is not None for count in token_counts),
        },
        "rows": rows,
    }


def main() -> None:
    """Load prompts, run benchmark and save a report with no API credentials."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--hardware", default=None, help="Actual GPU description for the report")
    parser.add_argument("--vllm-version", default=None, help="Actual vLLM version for the report")
    parser.add_argument("--input", type=Path, default=Path("evals/support_cases.jsonl"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--max-tokens", type=int, default=256)
    parser.add_argument("--timeout", type=float, default=120)
    args = parser.parse_args()
    args.api_key = os.getenv("VLLM_API_KEY", "")
    if args.concurrency < 1 or args.max_tokens < 1 or args.timeout <= 0:
        parser.error("concurrency, max-tokens and timeout must be positive")
    cases = [json.loads(line) for line in args.input.read_text(encoding="utf-8").splitlines() if line.strip()]
    if not cases or len({case.get("id") for case in cases}) != len(cases):
        parser.error("input must contain unique case IDs")
    if any(not isinstance(case.get("question"), str) or not case["question"].strip() for case in cases):
        parser.error("each case must contain a nonempty question")
    report = asyncio.run(benchmark(args, cases))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report["metrics"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
