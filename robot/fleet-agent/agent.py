#!/usr/bin/env python3
"""Fleet-agent entrypoint."""
import argparse

import uvicorn

from fleet_agent.app import create_app
from fleet_agent.config import load_config


def main():
    parser = argparse.ArgumentParser(description="Peacekeeper fleet-agent")
    parser.add_argument("--config", default=None, help="Path to YAML config")
    parser.add_argument("--host", default=None, help="Override bind host")
    parser.add_argument("--port", type=int, default=None, help="Override bind port")
    args = parser.parse_args()

    config = load_config(args.config)
    if args.host:
        config.host = args.host
    if args.port:
        config.port = args.port

    app = create_app(config)
    uvicorn.run(app, host=config.host, port=config.port, log_level="info")


if __name__ == "__main__":
    main()
