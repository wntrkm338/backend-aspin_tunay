from django.core.management.base import BaseCommand, CommandError
from django.db import connections

from accounts.security import secure_public_schema


class Command(BaseCommand):
    help = "Enable RLS and revoke public API table access for Supabase public schema tables."

    def add_arguments(self, parser):
        parser.add_argument(
            "--database",
            default="default",
            help="Database alias to harden. Defaults to 'default'.",
        )

    def handle(self, *args, **options):
        database_alias = options["database"]

        if database_alias not in connections.databases:
            raise CommandError(f"Unknown database alias: {database_alias}")

        connection = connections[database_alias]
        if not secure_public_schema(connection):
            self.stdout.write(
                self.style.WARNING(
                    f"Skipped database alias '{database_alias}' because it is not a Supabase/PostgreSQL connection."
                )
            )
            return

        self.stdout.write(
            self.style.SUCCESS(
                f"Public schema hardening completed for database alias '{database_alias}'."
            )
        )
