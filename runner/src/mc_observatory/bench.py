"""Sequential benchmark client; only the dashboard backend writes run artifacts."""

import argparse
import asyncio
import json
import time

import httpx


async def run_batch(client, *, task, models, tiers, timing, trials, label, max_steps=30, time_budget=180, poll_interval=1):
    if not models or not tiers or trials < 1 or not label:
        raise ValueError("Models, tiers, a positive trial count and a nonempty label are required")
    runs = []
    for model in models:
        for tier in tiers:
            for trial in range(1, trials + 1):
                spec = dict(task=task, model=model, tier=tier, timing=timing, label=label,
                            trial_index=trial, max_steps=max_steps, time_budget=time_budget)
                response = await client.post("/api/runs", json=spec)
                response.raise_for_status()
                run_id = response.json()["id"]
                print(json.dumps({"event": "started", "id": run_id, **spec}), flush=True)
                deadline = time.monotonic() + time_budget + 240
                while True:
                    response = await client.get(f"/api/runs/{run_id}")
                    response.raise_for_status()
                    detail = response.json()
                    if detail["status"] not in {"starting", "running"}:
                        break
                    if time.monotonic() > deadline:
                        raise TimeoutError(f"Backend run {run_id} did not finish; batch stopped, no overlapping run launched")
                    await asyncio.sleep(poll_interval)
                runs.append(detail)
                print(json.dumps({"event": "finished", "id": run_id, "status": detail["status"],
                                  "summary": detail.get("summary"), "metadata": detail.get("metadata")}), flush=True)
                if detail["status"] in {"error", "interrupted"}:
                    print(json.dumps({"event": "infrastructure_failure", "id": run_id}), flush=True)
    return runs


def main():
    parser = argparse.ArgumentParser(prog="mc-bench", description=__doc__)
    parser.add_argument("--task", default="get_log")
    parser.add_argument("--models", default="holo4-27b,holo4-35b-a3b")
    parser.add_argument("--tiers", default="raw,skills")
    parser.add_argument("--timing", choices=("freeze", "realtime"), default="realtime")
    parser.add_argument("--trials", type=int, default=5)
    parser.add_argument("--label", required=True)
    parser.add_argument("--max-steps", type=int, default=30)
    parser.add_argument("--time-budget", type=float, default=180)
    parser.add_argument("--backend", default="http://127.0.0.1:8000")
    args = parser.parse_args()
    if args.trials < 1: parser.error("--trials must be positive")
    async def run():
        async with httpx.AsyncClient(base_url=args.backend, timeout=30) as client:
            await run_batch(client, task=args.task, models=[v.strip() for v in args.models.split(",") if v.strip()],
                            tiers=[v.strip() for v in args.tiers.split(",") if v.strip()], timing=args.timing,
                            trials=args.trials, label=args.label, max_steps=args.max_steps, time_budget=args.time_budget)
    asyncio.run(run())


if __name__ == "__main__":
    main()
