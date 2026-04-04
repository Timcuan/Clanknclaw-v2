from pathlib import Path

from clankandclaw.config import load_config


def main() -> None:
    load_config(Path("config.yaml"))


if __name__ == "__main__":
    main()
