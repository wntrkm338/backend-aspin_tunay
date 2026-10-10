import os
from typing import Optional

from supabase import Client, create_client


def _supabase_key() -> Optional[str]:
    return (
        os.getenv("SUPABASE_KEY")
        or os.getenv("SUPABASE_ANON_KEY")
        or os.getenv("SUPABASE_SERVICE_ROLE_KEY")
    )


def get_supabase_client() -> Client:
    url = os.getenv("SUPABASE_URL")
    key = _supabase_key()

    if not url or not key:
        raise ValueError(
            "Supabase credentials are missing. Set SUPABASE_URL and "
            "SUPABASE_KEY, SUPABASE_ANON_KEY, or SUPABASE_SERVICE_ROLE_KEY."
        )

    return create_client(url, key)


supabase: Optional[Client] = None

_configured_url = os.getenv("SUPABASE_URL")
_configured_key = _supabase_key()

if _configured_url and _configured_key:
    supabase = create_client(_configured_url, _configured_key)
