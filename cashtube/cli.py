from __future__ import annotations

import argparse
import sys

import cashtube_pipeline
import digital_asset_ghost_hunter
import phase1_smart_discovery
import phase2_dead_link_detection


def main() -> None:
    parser = argparse.ArgumentParser(prog="cashtube")
    parser.add_argument("command", choices=["pipeline", "phase1", "phase2", "ghost"])
    if len(sys.argv) == 1:
        parser.print_help()
        raise SystemExit(2)

    args = parser.parse_args(sys.argv[1:2])
    command_map = {
        "pipeline": cashtube_pipeline.main,
        "phase1": phase1_smart_discovery.main,
        "phase2": phase2_dead_link_detection.main,
        "ghost": digital_asset_ghost_hunter.main,
    }
    sys.argv = [f"cashtube {args.command}", *sys.argv[2:]]
    command_map[args.command]()


if __name__ == "__main__":
    main()
