from __future__ import annotations

import unittest
from unittest.mock import patch
from uuid import UUID

from app.services.al_forno_onboarding import (
    build_al_forno_menu_import_request,
)
from app.services.menu_importer import (
    MenuImportError,
    import_structured_menu,
)


class _SupabaseError(Exception):
    code = "P0001"
    message = "IDEMPOTENCY_PAYLOAD_MISMATCH"
    details = None
    hint = None


class _FailingClient:
    def rpc(self, _name, _payload):
        return self

    def execute(self):
        raise _SupabaseError()


class MenuImporterTests(unittest.TestCase):
    def test_supabase_error_is_logged_and_mapped(self) -> None:
        request = build_al_forno_menu_import_request(
            restaurant_id=UUID(
                "10000000-0000-0000-0000-000000000001"
            ),
            provisioning_job_id=UUID(
                "10000000-0000-0000-0000-000000000002"
            ),
            idempotency_key=UUID(
                "10000000-0000-0000-0000-000000000003"
            ),
        )

        with patch(
            "app.services.menu_importer.get_client",
            return_value=_FailingClient(),
        ), self.assertLogs(
            "app.services.menu_importer",
            level="ERROR",
        ):
            with self.assertRaises(MenuImportError) as raised:
                import_structured_menu(request)

        self.assertEqual(
            raised.exception.code,
            "IDEMPOTENCY_PAYLOAD_MISMATCH",
        )


if __name__ == "__main__":
    unittest.main()
