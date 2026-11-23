"""Run the ShopBot test suite against a live API and print a markdown report.

Usage:
    python scripts/benchmark.py https://YOUR-SPACE.hf.space
    python scripts/benchmark.py http://localhost:8000 --tests tests.json --timeout 120

Every run gets a unique suffix on user and thread ids, so runs never see each
other's data. Tests run in file order (test 5 relies on the order from test 4,
test 8 reuses test 7's thread id with a different user).
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
import uuid
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent


def p95(values: list[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    rank = max(1, math.ceil(0.95 * len(ordered)))  # nearest-rank
    return ordered[rank - 1]


def md(text: str) -> str:
    return str(text).replace("|", "\\|").replace("\n", " ")


def tool_names(tool_calls) -> list[str]:
    names = []
    for t in tool_calls or []:
        names.append(t.get("name", "?") if isinstance(t, dict) else str(t))
    return names


def check_health(client: httpx.Client, base: str) -> None:
    try:
        r = client.get(f"{base}/health")
    except httpx.HTTPError as e:
        sys.exit(f"Health check failed: cannot reach {base}/health ({e})")
    body = r.text.strip().strip('"').lower()
    if r.status_code != 200 or "ok" not in body:
        sys.exit(f"Health check failed: {r.status_code} {r.text[:200]!r}")
    print(f"Health check OK ({base})\n")


def run_test(client: httpx.Client, base: str, test: dict, run_id: str) -> dict:
    called: list[str] = []
    latencies: list[float] = []
    tokens_in = tokens_out = 0
    reply = ""
    error = None

    for step in test["steps"]:
        payload = {
            "user_id": f"{step['user_id']}-{run_id}",
            "thread_id": f"{step['thread_id']}-{run_id}",
            "message": step["message"],
        }
        start = time.perf_counter()
        try:
            r = client.post(f"{base}/chat", json=payload)
        except httpx.HTTPError as e:
            error = f"request failed: {e.__class__.__name__}"
            break
        latencies.append((time.perf_counter() - start) * 1000)
        if r.status_code != 200:
            error = f"HTTP {r.status_code}: {r.text[:120]}"
            break
        try:
            data = r.json()
        except ValueError:
            error = "response was not JSON"
            break
        reply = data.get("reply") or ""
        called += tool_names(data.get("tool_calls"))
        tokens = data.get("tokens") or {}
        tokens_in += int(tokens.get("in") or 0)
        tokens_out += int(tokens.get("out") or 0)

    reasons = []
    if error:
        reasons.append(error)
    else:
        low = reply.lower()
        missing = [t for t in test.get("expected_tools", []) if t not in called]
        if missing:
            reasons.append("missing tool(s): " + ", ".join(missing))
        forbidden = [t for t in test.get("forbidden_tools", []) if t in called]
        if forbidden:
            reasons.append("should not call: " + ", ".join(forbidden))
        expect_any = test.get("expect_any", [])
        if expect_any and not any(s.lower() in low for s in expect_any):
            reasons.append("reply has none of: " + ", ".join(repr(s) for s in expect_any))
        hits = [s for s in test.get("expect_none", []) if s.lower() in low]
        if hits:
            reasons.append("reply contains: " + ", ".join(repr(s) for s in hits))
        # e.g. "max_tokens": 0 for messages the safety node must block before the model
        if "max_tokens" in test and tokens_in + tokens_out > test["max_tokens"]:
            reasons.append(f"used {tokens_in + tokens_out} tokens (max {test['max_tokens']})")

    return {
        "test": test,
        "passed": not reasons,
        "reason": "; ".join(reasons),
        "tools": called,
        "latencies": latencies,
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
        "reply": reply,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Benchmark the ShopBot API.")
    parser.add_argument("base_url", help="API base URL, e.g. https://YOUR-SPACE.hf.space")
    parser.add_argument("--tests", default=str(ROOT / "tests.json"), help="path to tests.json")
    parser.add_argument("--timeout", type=float, default=120.0, help="per-request timeout in seconds")
    parser.add_argument("--verbose", action="store_true", help="print each final reply")
    args = parser.parse_args()

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    base = args.base_url.rstrip("/")
    tests = json.loads(Path(args.tests).read_text(encoding="utf-8"))
    run_id = uuid.uuid4().hex[:8]

    results = []
    with httpx.Client(timeout=args.timeout) as client:
        check_health(client, base)
        for test in tests:
            print(f"Running #{test['id']} {test['category']}…", file=sys.stderr, flush=True)
            results.append(run_test(client, base, test, run_id))

    print(f"## ShopBot benchmark (run `{run_id}`)\n")
    print("| # | Test | Tools called | Latency | Result |")
    print("|---|------|--------------|---------|--------|")
    for r in results:
        t = r["test"]
        lat = sum(r["latencies"])
        steps = len(t["steps"])
        lat_txt = f"{lat:,.0f} ms" + (f" ({steps} steps)" if steps > 1 else "")
        tools = ", ".join(r["tools"]) or "—"
        result = "PASS" if r["passed"] else f"FAIL: {r['reason']}"
        print(f"| {t['id']} | {md(t['category'])} | {md(tools)} | {lat_txt} | {md(result)} |")

    all_lat = [x for r in results for x in r["latencies"]]
    passed = sum(r["passed"] for r in results)
    avg = sum(all_lat) / len(all_lat) if all_lat else 0.0
    tin = sum(r["tokens_in"] for r in results)
    tout = sum(r["tokens_out"] for r in results)

    print()
    print(f"**Passed:** {passed}/{len(results)}  ")
    print(f"**Average latency:** {avg:,.0f} ms per request ({len(all_lat)} requests)  ")
    print(f"**p95 latency:** {p95(all_lat):,.0f} ms  ")
    print(f"**Total tokens:** {tin + tout:,} ({tin:,} in / {tout:,} out)")

    if args.verbose:
        print("\n### Final replies\n")
        for r in results:
            print(f"**#{r['test']['id']}** {md(r['reply'])}\n")

    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
