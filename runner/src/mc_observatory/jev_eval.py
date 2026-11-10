"""Offline, pixels-only Jev evaluation. Never connects to Minecraft or sends labels."""

import argparse
import asyncio
import hashlib
import json
import os
import random
import statistics
import time
from pathlib import Path

from PIL import Image, ImageDraw

from .jev import PRICE_PER_INPUT_TOKEN, RESERVE_PER_CALL

LABELS = ("crosshair_on_log", "outline_visible", "log_in_hotbar", "stone_visible", "iron_visible", "nearest_trunk")
MOVES = ("forward", "turn_left", "turn_right", "jump", "attack", "stop")


def read_rows(path):
    return [json.loads(line) for line in Path(path).read_text().splitlines() if line.strip()]


def prepare(runs, output, count=200, seed=7941):
    if count < 1:
        raise ValueError("count must be positive")
    output = Path(output)
    if any((output / name).exists() for name in ("manifest.jsonl", "responses.jsonl", "labels.json")):
        raise ValueError("Refusing to overwrite existing dataset or paid results")
    (output / "frames").mkdir(parents=True, exist_ok=True)
    rng = random.Random(seed)
    groups = []
    for run in sorted(Path(runs).iterdir()):
        if not (run / "steps.jsonl").exists():
            continue
        candidates = []
        for record in read_rows(run / "steps.jsonl"):
            ref = record.get("screenshot_ref")
            if not ref or ref not in record.get("input_image_refs", []) or record.get("tokens", {}).get("in", 0) <= 0:
                continue
            path = (run / ref).resolve()
            if not path.is_relative_to(run.resolve()) or not path.is_file():
                continue
            candidates.append((run, record, path))
        rng.shuffle(candidates)
        if candidates:
            groups.append(candidates)
    selected, hashes = [], set()
    while len(selected) < count and any(groups):
        for group in groups:
            if not group or len(selected) == count:
                continue
            run, record, path = group.pop()
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            if digest not in hashes:
                hashes.add(digest)
                selected.append((run, record, path, digest))
    if len(selected) != count:
        raise ValueError(f"Only {len(selected)} unique proven API-input frames available; need {count}")
    rows = []
    for index, (run, record, path, digest) in enumerate(sorted(selected, key=lambda s: (s[0].name, s[1]["step"]))):
        image = f"frames/{index:03}.png"
        (output / image).write_bytes(path.read_bytes())
        labels = dict.fromkeys(LABELS)
        sources = {}
        if record["step"] == 0 and (run / "initial_state.json").exists():
            initial = json.loads((run / "initial_state.json").read_text()).get("state", {})
            if "inventory" in initial:
                labels["log_in_hotbar"] = any(0 <= item.get("slot", -1) <= 8 and item["id"].endswith("_log")
                                                  and item.get("count", 0) > 0 for item in initial["inventory"])
                sources["log_in_hotbar"] = "initial_state.inventory aligned to first pre-action frame"
        rows.append({"id": index, "image": image, "sha256": digest, "run": run.name,
                     "step": record["step"], "source_ref": record["screenshot_ref"],
                     "source_input_refs": record["input_image_refs"], "labels": labels, "label_sources": sources})
    (output / "manifest.jsonl").write_text("".join(json.dumps(row) + "\n" for row in rows))
    sheets(output, rows)
    return rows


def sheets(root, rows):
    for first in range(0, len(rows), 20):
        canvas = Image.new("RGB", (2048, 1600), "#151515")
        draw = ImageDraw.Draw(canvas)
        for offset, row in enumerate(rows[first:first + 20]):
            image = Image.open(root / row["image"]).convert("RGB")
            w, h = image.size
            x, y = offset % 4 * 512, offset // 4 * 320
            draw.text((x + 3, y + 2), f'{row["id"]:03} {row["run"][-8:]} step{row["step"]}', fill="white")
            canvas.paste(image.resize((384, 216)), (x, y + 18))
            canvas.paste(image.crop((w // 2 - 64, h // 2 - 64, w // 2 + 64, h // 2 + 64)), (x + 384, y + 18))
            canvas.paste(image.crop((max(0, w // 2 - 200), h - 58, min(w, w // 2 + 200), h)).resize((512, 74)), (x, y + 235))
        canvas.save(root / f"sheet-{first // 20:02}.png")


def questions():
    from typesafe_sdk import Box, Choice, Noul
    return {
        "crosshair_on_log": Noul(instructions="Minecraft screenshot: Is the center crosshair aimed at a tree LOG block with a visible black selection outline? Require both bark at crosshair AND outline. Exclude leaves, dirt, planks, crafting tables, and the held item in the lower-right. GUI screens mean no."),
        "log_in_hotbar": Noul(instructions="Is at least one tree LOG item visible in a HOTBAR slot? Count the nine-slot HUD or the inventory GUI's bottom hotbar row. Exclude planks, crafting tables, sticks, and held-item sprites; don't count main inventory slots."),
        "nearest_trunk": Box(instructions="Box tightly around exposed bark of the nearest visible standing tree trunk, using apparent size to judge nearness. Exclude leaves, crafting tables, logs held in the hand, and GUI item icons. Include only its visible bark region, not the canopy."),
        "next_move": Choice(instructions="Choose a safe immediate reflex toward obtaining a tree log: attack only if crosshair is on reachable bark; forward if facing bark but too far; turn toward visible trunk if off-center; jump for a small obstacle. Stop if GUI open, hazard, or no safe visible target.", criteria={move: None for move in MOVES}),
    }


class Budget:
    def __init__(self, limit=1.8):
        if not 0 < limit <= 1.8:
            raise ValueError("budget must be in (0,1.8], leaving margin below $2")
        self.limit, self.spent, self.reserved = limit, 0., 0.
        self.lock = asyncio.Lock()

    async def reserve(self):
        async with self.lock:
            if self.spent + self.reserved + RESERVE_PER_CALL > self.limit:
                raise RuntimeError("Budget exhausted")
            self.reserved += RESERVE_PER_CALL

    async def settle(self, input_tokens=None):
        async with self.lock:
            cost = RESERVE_PER_CALL if input_tokens is None else input_tokens * PRICE_PER_INPUT_TOKEN
            self.reserved -= RESERVE_PER_CALL
            self.spent += cost
            return cost


async def evaluate(root, *, limit=1.8, model="jev-1.14.0", client=None):
    from typesafe_sdk import AsyncTypeSafeClient, Image as Asset, RetryPolicy
    root = Path(root)
    rows = read_rows(root / "manifest.jsonl")
    if not rows:
        raise ValueError("Empty manifest")
    for row in rows:
        path = (root / row["image"]).resolve()
        if not path.is_relative_to(root.resolve()) or hashlib.sha256(path.read_bytes()).hexdigest() != row["sha256"]:
            raise ValueError("Frame provenance/hash mismatch")
    budget = Budget(limit)
    abort = asyncio.Event()
    output = root / "responses.jsonl"
    if output.exists():
        raise ValueError("Refusing to overwrite paid results; use a new dataset directory")
    own_client = client is None
    if own_client:
        client = AsyncTypeSafeClient(model=model, headers={"TypeSafe-Danger-Zone-Images": "true"},
                                     retry=RetryPolicy(max_retries=0), timeout=15)
    try:
        with output.open("x") as file:
            for concurrency in (1, 4):
                sem = asyncio.Semaphore(concurrency)

                async def one(row):
                    async with sem:
                        if abort.is_set():
                            return
                        try:
                            await budget.reserve()
                        except RuntimeError:
                            abort.set()
                            return
                        start = time.perf_counter()
                        record = {"id": row["id"], "concurrency": concurrency}
                        try:
                            response = await client.system_one(model=model, state={"screenshot": Asset(root / row["image"])}, questions=questions())
                            record["latency_ms"] = (time.perf_counter() - start) * 1000
                            record.update(model=response.model, input_tokens=response.usage.input_tokens,
                                          output_tokens=response.usage.output_tokens)
                            record["cost_usd"] = await budget.settle(response.usage.input_tokens)
                            record["nouls"] = {key: response.nouls[key].noul for key in ("crosshair_on_log", "log_in_hotbar")}
                            box = response.boxes["nearest_trunk"].box
                            record["box"] = [box.left, box.top, box.right, box.bottom]
                            record["next_move"] = response.choices["next_move"].choice
                        except Exception as error:
                            record["latency_ms"] = (time.perf_counter() - start) * 1000
                            key = os.environ.get("TYPESAFE_API_KEY")
                            message = str(error)
                            if key:
                                message = message.replace(key, "[REDACTED]")
                            record["error"] = {"type": type(error).__name__, "message": message[:2000], "status_code": getattr(error, "status_code", None)}
                            abort.set()
                            if "cost_usd" not in record:
                                record["cost_upper_bound_usd"] = await budget.settle()
                        file.write(json.dumps(record) + "\n")
                        file.flush()
                await asyncio.gather(*(one(row) for row in rows))
                print(json.dumps({"phase_done": concurrency, "aborted": abort.is_set(), "budget_charged_or_reserved_usd": budget.spent}), flush=True)
                if abort.is_set():
                    break
    finally:
        if own_client:
            await client.aclose()
    return summarize(root)


def binary_metrics(pairs):
    if not pairs:
        return {"n": 0, "accuracy": None, "auc": None}
    pos, neg = [p for p, y in pairs if y], [p for p, y in pairs if not y]
    auc = sum((p > n) + .5 * (p == n) for p in pos for n in neg) / (len(pos) * len(neg)) if pos and neg else None
    tp = sum(p >= .5 for p in pos)
    fp = sum(p >= .5 for p in neg)
    return {"n": len(pairs), "positive": len(pos), "accuracy": sum((p >= .5) == y for p, y in pairs) / len(pairs), "auc": auc,
            "tp": tp, "fp": fp, "tn": len(neg) - fp, "fn": len(pos) - tp,
            "precision": tp / (tp + fp) if tp + fp else None, "recall": tp / len(pos) if pos else None,
            "false_positive_rate": fp / len(neg) if neg else None, "majority_baseline_accuracy": max(len(pos), len(neg)) / len(pairs)}


def iou(a, b):
    intersect = max(0, min(a[2], b[2]) - max(a[0], b[0])) * max(0, min(a[3], b[3]) - max(a[1], b[1]))
    union = max(0, a[2] - a[0]) * max(0, a[3] - a[1]) + max(0, b[2] - b[0]) * max(0, b[3] - b[1]) - intersect
    return intersect / union if union else 0


def percentile(values, p):
    values = sorted(values)
    if not values:
        return None
    index = (len(values) - 1) * p
    lo, hi = int(index), min(len(values) - 1, int(index) + 1)
    return values[lo] + (values[hi] - values[lo]) * (index - lo)


def summarize(root):
    root = Path(root)
    labels = {row["id"]: row["labels"] for row in read_rows(root / "manifest.jsonl")}
    overrides = root / "labels.json"
    if overrides.exists():
        for key, value in json.loads(overrides.read_text()).items():
            labels[int(key)].update(value)
    records = read_rows(root / "responses.jsonl")
    report = []
    for concurrency in (1, 4):
        phase = [r for r in records if r["concurrency"] == concurrency]
        ok = [r for r in phase if "error" not in r]
        boxes = [(r["box"], labels[r["id"]]["nearest_trunk"]) for r in ok if labels[r["id"]]["nearest_trunk"] is not None]
        row = {"concurrency": concurrency, "calls": len(phase), "successes": len(ok), "failures": len(phase) - len(ok),
               "p50_ms": percentile([r["latency_ms"] for r in ok], .5), "p90_ms": percentile([r["latency_ms"] for r in ok], .9),
               "cost_usd": sum(r.get("cost_usd", 0) for r in phase), "cost_upper_bound_usd": sum(r.get("cost_usd", r.get("cost_upper_bound_usd", 0)) for r in phase),
               "box_n": len(boxes), "mean_iou": statistics.mean(iou(a, b) for a, b in boxes) if boxes else None,
               "center_in_trunk": statistics.mean(b[0] <= (a[0] + a[2]) / 2 <= b[2] and b[1] <= (a[1] + a[3]) / 2 <= b[3] for a, b in boxes) if boxes else None}
        for key in ("crosshair_on_log", "log_in_hotbar"):
            row[key] = binary_metrics([(r["nouls"][key], labels[r["id"]][key]) for r in ok if labels[r["id"]][key] is not None])
        row["mean_cost_per_call_usd"] = row["cost_usd"] / len(phase) if phase else None
        report.append(row)
    (root / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("prepare", "evaluate", "summarize"))
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--runs", type=Path, default=Path("runs"))
    parser.add_argument("--count", type=int, default=200)
    args = parser.parse_args()
    if args.command == "prepare":
        result = {"prepared": len(prepare(args.runs, args.dataset, args.count))}
    elif args.command == "evaluate":
        result = asyncio.run(evaluate(args.dataset))
    else:
        result = summarize(args.dataset)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
