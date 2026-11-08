from __future__ import annotations

import argparse
import asyncio
import json
import os
import secrets
from pathlib import Path

import uvicorn

from .analysis import analyze_run, compare_runs
from .app import create_app
from .config import Settings
from .devin import build_analysis_prompt, get_identity, get_session, submit_analysis
from .recording import read_events


def main() -> None:
    parser = argparse.ArgumentParser(description="HoloSpeed visual Minecraft agent")
    sub = parser.add_subparsers(dest="command", required=True)
    serve = sub.add_parser("serve", help="Start the local controller and spectator console")
    serve.add_argument("--mode", choices=["demo", "live"], default=None)
    serve.add_argument("--port", type=int, default=None)
    init = sub.add_parser("init-bridge", help="Create a local bridge token and mod config")
    init.add_argument("--minecraft-dir", type=Path, required=True)
    analyze = sub.add_parser("analyze", help="Prepare or explicitly submit a run digest to Devin")
    analyze.add_argument("run_dir", type=Path)
    analyze.add_argument(
        "--submit", action="store_true", help="Create a Devin v3 session, capped at 2 ACU"
    )
    analyze.add_argument(
        "--context-file",
        type=Path,
        help="Optional JSON engineering notes (current_changes, verification, known_limitations, analysis_focus, source_revision)",
    )
    report = sub.add_parser("report", help="Analyze a local run journal without model API calls")
    report.add_argument("run_dir", type=Path)
    compare = sub.add_parser("compare", help="Compare two or more local run journals")
    compare.add_argument("run_dirs", type=Path, nargs="+")
    sub.add_parser("devin-identity", help="Read Devin account memberships to find DEVIN_ORG_ID")
    session = sub.add_parser("devin-session", help="Read a previously submitted Devin session")
    session.add_argument("session_id")
    args = parser.parse_args()
    settings = Settings.from_env()
    if args.command == "serve":
        settings.mode = args.mode or settings.mode
        settings.port = args.port or settings.port
        if settings.host not in {"127.0.0.1", "localhost", "::1"}:
            parser.error("The game controller must bind to loopback")
        if settings.mode == "live" and not os.getenv("HOLOSPEED_BRIDGE_TOKEN"):
            parser.error("Run holospeed init-bridge --minecraft-dir <game-directory> first")
        print(f"HoloSpeed {settings.mode.upper()} • http://{settings.host}:{settings.port}")
        uvicorn.run(
            create_app(settings), host=settings.host, port=settings.port, ws_max_size=16_000_000
        )
    elif args.command == "init-bridge":
        token = os.getenv("HOLOSPEED_BRIDGE_TOKEN") or secrets.token_urlsafe(32)
        if not os.getenv("HOLOSPEED_BRIDGE_TOKEN"):
            env_path = Path(".env")
            with env_path.open("a", encoding="utf-8") as stream:
                stream.write(f"\nHOLOSPEED_BRIDGE_TOKEN={token}\n")
            env_path.chmod(0o600)
        path = args.minecraft_dir / "config" / "holospeed.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({"url": f"ws://127.0.0.1:{settings.port}/bridge", "token": token}, indent=2)
            + "\n"
        )
        path.chmod(0o600)
        print(f"Bridge configured in {path}. Restart Minecraft after installing the mod.")
    elif args.command == "analyze":
        summary = json.loads((args.run_dir / "summary.json").read_text())
        events = read_events(args.run_dir / "events.jsonl", exclude_model=True)
        context = json.loads(args.context_file.read_text()) if args.context_file else None
        if context is not None and not isinstance(context, dict):
            parser.error("--context-file must contain a JSON object")
        if args.submit:
            print(
                json.dumps(
                    asyncio.run(
                        submit_analysis(settings, summary, events, engineering_context=context)
                    ),
                    indent=2,
                )
            )
        else:
            print(build_analysis_prompt(summary, events, context))
    elif args.command == "report":
        print(json.dumps(analyze_run(args.run_dir), indent=2))
    elif args.command == "compare":
        if len(args.run_dirs) < 2:
            parser.error("compare requires at least two run directories")
        print(json.dumps(compare_runs(args.run_dirs), indent=2))
    elif args.command == "devin-identity":
        print(json.dumps(asyncio.run(get_identity(settings)), indent=2))
    elif args.command == "devin-session":
        print(json.dumps(asyncio.run(get_session(settings, args.session_id)), indent=2))


if __name__ == "__main__":
    main()
