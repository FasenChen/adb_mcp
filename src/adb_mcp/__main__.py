"""Explicit-config CLI; all diagnostics go to stderr."""

import argparse
import logging
import sys
from pathlib import Path

from .config import load_config
from .server import create_server


def main() -> None:
    parser = argparse.ArgumentParser(description="Allowlisted fixed-target ADB MCP (stdio)")
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--check-config", action="store_true", help="validate without starting ADB")
    args = parser.parse_args()
    logging.basicConfig(
        stream=sys.stderr,
        level=logging.WARNING,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    try:
        config = load_config(args.config)
    except (OSError, ValueError) as exc:
        # Do not echo potentially sensitive configuration values from validation errors.
        print(
            f"Configuration invalid ({type(exc).__name__}); check config file/schema",
            file=sys.stderr,
        )
        raise SystemExit(2) from None
    if args.check_config:
        print(f"Configuration valid; exact target={config.target_serial}", file=sys.stderr)
        return
    create_server(config).run(transport="stdio")


if __name__ == "__main__":
    main()
