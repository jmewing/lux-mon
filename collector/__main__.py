"""Run the collector as a module: python -m collector"""
import argparse
import sys
from .collector import run_collector


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m collector",
        description="Passive LuxPower/EG4 TCP collector",
    )
    parser.add_argument("--config", "-c", help="Path to config file")
    parser.add_argument("--replay", help="Replay a captured binary file instead of live TCP")
    parser.add_argument("--interval", type=int, help="InfluxDB write interval (seconds)")
    parser.add_argument("--host", help="Dongle host")
    parser.add_argument("--port", type=int, help="Dongle port")
    parser.add_argument("--log-level", default="INFO", help="Logging level (DEBUG/INFO/WARNING/ERROR)")
    return parser


if __name__ == "__main__":
    parser = _build_parser()
    args = parser.parse_args(sys.argv[1:])

    overrides = {}
    if args.replay:
        overrides["replay_file"] = args.replay
    if args.interval:
        overrides["write_interval"] = args.interval
    if args.host:
        overrides["dongle_host"] = args.host
    if args.port:
        overrides["dongle_port"] = args.port

    run_collector(
        config_path=args.config,
        log_level=args.log_level.upper(),
        overrides=overrides,
    )
