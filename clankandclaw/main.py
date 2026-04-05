from pathlib import Path

from clankandclaw.config import load_config
from clankandclaw.core.supervisor import Supervisor


def main() -> None:
    load_config(Path("config.yaml"))
    Supervisor(["x", "gmgn", "pipeline", "telegram", "clanker"])
