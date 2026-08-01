"""Run the collector as a module: python -m collector"""
import argparse
import sys
from .collector import run_collector


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m collector",
        description="LuxPower/EG4 TCP collector (passive listen or active polling)",
    )
    parser.add_argument("--config", "-c", help="Path to config file")
    parser.add_argument("--replay", help="Replay a captured binary file instead of live TCP")
    parser.add_argument("--interval", type=int, help="Storage write interval (seconds)")
    parser.add_argument("--host", help="Dongle host")
    parser.add_argument("--port", type=int, help="Dongle port")
    parser.add_argument(
        "--poll", action="store_true",
        help="Active polling mode: send ReadInput requests (requires --datalog-serial and --inverter-serial)"
    )
    parser.add_argument("--poll-interval", type=float, default=2.0,
                        help="Seconds between poll requests (default: 2.0)")
    parser.add_argument("--poll-reg-start", type=int, default=0,
                        help="First register to poll (default: 0)")
    parser.add_argument("--poll-reg-count", type=int, default=40,
                        help="Registers per poll request (default: 40)")
    parser.add_argument("--datalog-serial", help="Datalog serial (required for --poll)")
    parser.add_argument("--inverter-serial", help="Inverter serial (required for --poll)")
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
    if args.poll:
        overrides["poll_mode"] = True
    if args.poll_interval:
        overrides["poll_interval"] = args.poll_interval
    if args.poll_reg_start:
        overrides["poll_register_start"] = args.poll_reg_start
    if args.poll_reg_count:
        overrides["poll_register_count"] = args.poll_reg_count
    if args.datalog_serial:
        overrides["datalog_serial"] = args.datalog_serial
    if args.inverter_serial:
        overrides["inverter_serial"] = args.inverter_serial

    run_collector(
        config_path=args.config,
        log_level=args.log_level.upper(),
        overrides=overrides,
    )
