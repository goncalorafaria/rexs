"""Opt-in adapter for programs that submit Beaker experiment specs."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from rexs.cli import Rexs


def main() -> None:
    parser = argparse.ArgumentParser(prog="beaker", description=__doc__)
    parser.add_argument("--format", choices=("json",), help="Beaker JSON compatibility")
    commands = parser.add_subparsers(dest="command", required=True)
    experiment = commands.add_parser("experiment")
    actions = experiment.add_subparsers(dest="action", required=True)
    create = actions.add_parser("create")
    create.add_argument("spec")
    create.add_argument("-n", "--name")
    create.add_argument("-w", "--workspace", help="Beaker workspace metadata; Slurm account comes from the Rex profile")
    create.add_argument("--profile", default=os.environ.get("REXS_PROFILE"))
    create.add_argument("--dry-run", action="store_true", default=os.environ.get("REXS_DRY_RUN") == "1")
    stop = actions.add_parser("stop")
    stop.add_argument("identifier")
    stop.add_argument("--dry-run", action="store_true", default=os.environ.get("REXS_DRY_RUN") == "1")
    args = parser.parse_args()
    if args.action == "stop":
        result = ({"backend": "rexs", "cancelled": False, "id": args.identifier}
                  if args.dry_run else Rexs().cancel(args.identifier))
        print(json.dumps(result, indent=2))
        return
    if not args.profile:
        parser.error("set REXS_PROFILE or pass --profile to select the Slurm cluster")
    rex = Rexs()
    if args.dry_run:
        output = str(Path(args.spec).with_suffix(".sbatch"))
        script = rex.render(args.spec, profile=args.profile, name=args.name, output=output)
        result = {"backend": "rexs", "submitted": False, "script": script}
    else:
        result = rex.submit(args.spec, profile=args.profile, name=args.name)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
