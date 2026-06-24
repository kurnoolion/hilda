"""Example per-customer Google Drive adapter subclass -- TEMPLATE ONLY.

This file demonstrates the structure of a per-customer subclass per [D-027]
Teacher/Student split + [D-116] Ratified 2026-06-25. Copy this file to
`<customer_id>_adapter.py` and fill in the `TODO(cline)` markers on Work PC
with the actual binding import + call.

NFR-2: NEVER commit the concrete binding-import line to public github.
Real per-customer subclasses live only on Work PC (Cline's domain).
"""
from __future__ import annotations

import asyncio
from pathlib import Path

from core.src.customer_adapter import GoogleDriveBaseAdapter


class ExampleCustomerAdapter(GoogleDriveBaseAdapter):
    """Example per-customer Google Drive adapter.

    Bake in the customer identity via class-level overrides:
    - `source_system` + `customer_id` = the customer_id slug
    - `pm_id` = the shared HILDA ops-team Google account user_id

    Override `_invoke_binding(...)` with the binding import + call.
    """

    # TODO(cline): set the real customer_id slug here (e.g., "carrier_alpha").
    source_system: str = "example_customer"
    customer_id: str = "example_customer"

    # TODO(cline): set the shared HILDA ops-team Google account user_id here.
    # Sourced from per-customer .env / sops vault per [D-019] + [D-038].
    pm_id: str = "hilda-ops-team@example.invalid"

    async def _invoke_binding(
        self,
        device_id: str,
        milestone_name: str,
        source_dir: Path,
        target_dir: str,
        filename: str,
        pm_id: str,
        pm_password: str,
        totp_code: str,
        customer_delivery_info: str,
    ) -> bool:
        """Invoke the user's pre-existing Google Drive binding.

        TODO(cline): replace the body below with the real binding import + call:

            from <binding_module> import uploadAttachment  # noqa: N802
            return await asyncio.to_thread(
                uploadAttachment,
                device_id,                # Model_No
                milestone_name,           # milestone YAML key
                str(source_dir),          # LOCAL NSD path
                target_dir,               # Drive subdirectory (per-item target_folder)
                filename,                 # basename only
                pm_id,                    # cred.username (shared ops-team Google id)
                pm_password,              # cred.password
                totp_code,                # ephemeral 6-digit code from pyotp
                customer_delivery_info,   # per-row Drive root from Deliverables SP list
            )

        9-arg binding signature per D-126 cascade 2026-06-26 (closes [D-116]
        D13 follow-up). The binding composes the full Drive path internally per
        `<customer_delivery_info>/<Model_No>/<milestone_name>/<target_dir>/<filename>`
        (was binding-baked customer-root pre-D-126).

        Until the real binding is wired in, the base class raises
        NotImplementedError (CAD-E009) -- that's the Ph-1 expected behavior on
        Personal PC; tests use MockCustomerAdapter instead.
        """
        # Stub body: defer to base class which raises NotImplementedError (CAD-E009).
        # When wiring in the real binding on Work PC, replace this entire body.
        _ = (device_id, milestone_name, source_dir, target_dir, filename,
             pm_id, pm_password, totp_code, customer_delivery_info, asyncio)  # silence unused-arg lints
        return await super()._invoke_binding(
            device_id=device_id,
            milestone_name=milestone_name,
            source_dir=source_dir,
            target_dir=target_dir,
            filename=filename,
            pm_id=pm_id,
            pm_password=pm_password,
            totp_code=totp_code,
            customer_delivery_info=customer_delivery_info,
        )
