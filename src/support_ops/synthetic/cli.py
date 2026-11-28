"""Command-line entry point: ``python -m support_ops.synthetic <command>``.

Targets are chosen by name only (``--target development|test``); no command accepts a
database URL. Errors print only messages that never contain connection details.
"""

import argparse
import sys
from collections.abc import Sequence

from sqlalchemy.exc import SQLAlchemyError

from support_ops.config import DatabaseConfigError
from support_ops.synthetic.dataset import prepare_dataset
from support_ops.synthetic.dataset_config import MANIFEST_PATH
from support_ops.synthetic.manifest import check_committed_manifest, write_manifest
from support_ops.synthetic.reconcile import ReconciliationError
from support_ops.synthetic.records import SyntheticDatasetError
from support_ops.synthetic.seeding import SeedError, SeedTarget, run_seed


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m support_ops.synthetic",
        description="Deterministic synthetic data for the fictional demo store.",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser(
        "validate", help="Generate and validate in memory; check the committed manifest."
    )
    commands.add_parser("manifest", help="Regenerate data/golden/scenarios.json.")
    targets = [target.value for target in SeedTarget]
    seed = commands.add_parser("seed", help="Seed an empty target (no-op if identical).")
    seed.add_argument("--target", required=True, choices=targets)
    reset = commands.add_parser(
        "reset-seed", help="Guarded destructive reset of a target, then seed."
    )
    reset.add_argument("--target", required=True, choices=targets)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "validate":
            prepared = prepare_dataset()
            check_committed_manifest(prepared.manifest)
            manifest = prepared.manifest
            print(
                f"Synthetic dataset {manifest.dataset_version} is valid (seed "
                f"{manifest.random_seed}, anchor {manifest.anchor_time.isoformat()}).\n"
                f"Row counts: {manifest.row_counts}\n"
                f"Fingerprint: {manifest.dataset_fingerprint}\n"
                f"Golden scenarios: {len(manifest.scenarios)}; committed manifest is current."
            )
        elif args.command == "manifest":
            prepared = prepare_dataset()
            write_manifest(prepared.manifest)
            print(f"Wrote {MANIFEST_PATH.relative_to(MANIFEST_PATH.parents[2])}.")
        else:
            run_seed(SeedTarget(args.target), reset=args.command == "reset-seed")
    except (DatabaseConfigError, SyntheticDatasetError, SeedError, ReconciliationError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except SQLAlchemyError as exc:
        # Driver messages can include the host; report only the failure type.
        print(f"error: database operation failed ({type(exc).__name__}).", file=sys.stderr)
        return 1
    return 0
