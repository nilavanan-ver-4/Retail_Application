from django.core.management.base import BaseCommand, CommandError

from apps.core import legacy_import


class Command(BaseCommand):
    help = ("Copy the legacy Flask database (LEGACY_DATABASE_URL) into the Django schema, preserving "
            "primary keys. The legacy database is opened read-only and never modified.")

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true",
                            help="Load and verify everything, then roll back.")
        parser.add_argument("--allow-quarantine", action="store_true",
                            help="Import all valid rows even if some legacy rows violate the new "
                                 "constraints (they are listed in the report).")
        parser.add_argument("--report", default="legacy_import_report.json",
                            help="Where to write the JSON verification report.")
        parser.add_argument("--source", default="legacy", help="Database alias of the legacy DB.")

    def handle(self, *args, **o):
        try:
            rep = legacy_import.run(source=o["source"], dry_run=o["dry_run"],
                                    allow_quarantine=o["allow_quarantine"], report_path=o["report"])
        except legacy_import.LegacyImportError as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(self.style.SUCCESS("Legacy import finished"
                                             + (" (dry run, rolled back)" if o["dry_run"] else "")))
        for k, v in rep.counts_source.items():
            self.stdout.write(f"  {k:<18} source={v:<6} target={rep.counts_target.get(k, '-')}")
        for k, v in rep.totals.items():
            self.stdout.write(f"  {k}: {v}")
        self.stdout.write(f"  anomalies: {len(rep.anomalies)}  quarantined rows: {len(rep.quarantine)}")
        self.stdout.write(f"  report written to {o['report']}")
