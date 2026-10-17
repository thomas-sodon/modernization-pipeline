"""manage.py import_dataset DIR [--flush] (docs/contracts.md#feature-app-contract).

Loads a dataset directory (dataset.json plus tables/*.csv) into the legacy tables
through legacy/resources.py, and legacy/import_map.py when it exists. stdout carries
per-table counts only; row errors name the table, row number and column, never a
value. Exit 2 when dataset.json is unreadable or the target is refused (a dataset of
kind engagement on a local database whose name does not start with mpdata_ or whose
owner is not postgres, sa or mp_runner), exit 1 when rows fail (nothing is loaded).

Agents run it on synthetic seeds only; engagement rows load only through mp-data or
the operator, into an mpdata_ database or the client's staging database.
"""

from django.core.management.base import BaseCommand
from django.core.management.base import CommandError
from django.db import DEFAULT_DB_ALIAS

from ...loader import LoadError  # noqa: TID252
from ...loader import load_dataset  # noqa: TID252
from ...loader import summary_lines  # noqa: TID252


class Command(BaseCommand):
    help = (
        "Load a dataset directory (dataset.json and tables/*.csv) into the legacy "
        "tables. Prints per-table counts only."
    )

    def add_arguments(self, parser):
        parser.add_argument("directory", help="the dataset directory")
        parser.add_argument(
            "--flush",
            action="store_true",
            help="delete the legacy rows first (reverse load order)",
        )
        parser.add_argument(
            "--database",
            default=DEFAULT_DB_ALIAS,
            help="database alias (default: default)",
        )

    def handle(self, *args, **options):
        try:
            result = load_dataset(
                options["directory"],
                flush=options["flush"],
                using=options["database"],
            )
        except LoadError as exc:
            for problem in exc.problems:
                self.stderr.write(problem)
            raise CommandError(str(exc), returncode=exc.exit_code) from None
        for table, count in result.flushed.items():
            self.stdout.write(f"flushed {table}: {count}")
        for line in summary_lines(result):
            self.stdout.write(line)
        total = sum(result.counts.values())
        self.stdout.write(
            f"loaded {result.dataset}: {len(result.counts)} tables, {total} rows",
        )
