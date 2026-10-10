import logging
logger = logging.getLogger(__name__)


def should_secure_public_schema(connection):
    if connection.vendor != "postgresql":
        return False

    host = str(connection.settings_dict.get("HOST", "") or "").lower()
    alias = str(connection.alias or "").lower()

    return (
        alias == "supabase"
        or "supabase.co" in host
        or "supabase.com" in host
    )


def secure_public_schema(connection):
    if not should_secure_public_schema(connection):
        return False

    quote_name = connection.ops.quote_name

    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT tablename
            FROM pg_tables
            WHERE schemaname = 'public'
            ORDER BY tablename
            """
        )
        table_names = [row[0] for row in cursor.fetchall()]

        for table_name in table_names:
            qualified_table = f"{quote_name('public')}.{quote_name(table_name)}"
            cursor.execute(f"ALTER TABLE {qualified_table} ENABLE ROW LEVEL SECURITY")
            cursor.execute(
                f"REVOKE ALL PRIVILEGES ON TABLE {qualified_table} "
                "FROM PUBLIC, anon, authenticated"
            )

        cursor.execute(
            "REVOKE ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public "
            "FROM PUBLIC, anon, authenticated"
        )

    logger.info(
        "Secured %s public schema table(s) for database alias '%s'.",
        len(table_names),
        connection.alias,
    )
    return True
