from __future__ import annotations

import os
import secrets
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv


@dataclass
class Settings:
    mode: str = "demo"
    host: str = "127.0.0.1"
    port: int = 8765
    runs_dir: Path = field(default_factory=lambda: Path("runs"))
    bridge_token: str = field(default_factory=lambda: secrets.token_urlsafe(32))
    holo_api_key: str = ""
    holo_model: str = "holo4-35b-a3b"
    holo_base_url: str = "https://api.hcompany.ai/v1"
    holo_reasoning_effort: str = "none"
    jev_api_key: str = ""
    jev_base_url: str = "https://api.typesafe.ai"
    jev_model: str = "jev-1.14.0"
    devin_api_key: str = ""
    devin_org_id: str = ""
    planner_interval: int = 4
    model_timeout_s: float = 30
    frame_ttl_ms: int = 15000
    max_actions: int = 300
    max_run_seconds: int = 1800
    validate_holo_plans: bool = True
    declared_seed: str = ""

    @classmethod
    def from_env(cls) -> Settings:
        load_dotenv()
        return cls(
            mode=os.getenv("HOLOSPEED_MODE", "demo"),
            host=os.getenv("HOLOSPEED_HOST", "127.0.0.1"),
            port=int(os.getenv("HOLOSPEED_PORT", "8765")),
            runs_dir=Path(os.getenv("HOLOSPEED_RUNS_DIR", "runs")),
            bridge_token=os.getenv("HOLOSPEED_BRIDGE_TOKEN") or secrets.token_urlsafe(32),
            holo_api_key=os.getenv("HCOMPANY_API_KEY", ""),
            holo_model=os.getenv("HOLO_MODEL", "holo4-35b-a3b"),
            holo_base_url=os.getenv("HOLO_BASE_URL", "https://api.hcompany.ai/v1"),
            holo_reasoning_effort=os.getenv("HOLO_REASONING_EFFORT", "none"),
            jev_api_key=os.getenv("JEV_API_KEY", ""),
            jev_base_url=os.getenv("JEV_BASE_URL", "https://api.typesafe.ai"),
            jev_model=os.getenv("JEV_MODEL", "jev-1.14.0"),
            devin_api_key=os.getenv("DEVIN_API_KEY", ""),
            devin_org_id=os.getenv("DEVIN_ORG_ID", ""),
            model_timeout_s=float(os.getenv("MODEL_TIMEOUT_SECONDS", "30")),
            max_actions=int(os.getenv("MAX_ACTIONS", "300")),
            max_run_seconds=int(os.getenv("MAX_RUN_SECONDS", "1800")),
            validate_holo_plans=os.getenv("VALIDATE_HOLO_PLANS", "true").lower() == "true",
            declared_seed=os.getenv("MINECRAFT_SEED", ""),
        )
