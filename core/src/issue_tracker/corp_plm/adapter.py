"""CorpPlmAdapterBase -- HILDA-side thin wrapper around the 5 corp PLM APIs.

Per Module #13 cascade D3 + `[D-027]` Teacher/Student split 2026-06-25.

HILDA-side base class lives in core/; per-customer concrete subclass at
`customizations/issue_tracker/<customer_id>_corp_plm_adapter.py` overrides the
abstract `_invoke_*` methods with the actual corp PLM binding call body.
NEVER lands on public github per NFR-2.

Default `_invoke_*` impls raise NotImplementedError ITR-E001 so tests against
the base class fail loudly when not subclassed.

HILDA owns:
- Retry with exponential backoff (Q5; default N=5)
- In-flight tracker integration (Q6; via InFlightDownloadTracker passed to poller)
- CommunicationLog discipline (NFR-2; bounded enum tokens only)
- Parameter normalization (tpm_corp_id derivation per Q4 via utils.derive_tpm_corp_id)

Binding owns:
- Actual call to the corp_plm_gateway PC per `[D-019]`
- Auth + protocol + error codes
"""
from __future__ import annotations

import asyncio
from pathlib import Path

from core.src.diagnostics.error_codes import PipelineError
from core.src.issue_tracker.protocol import PlmDocumentNode


class CorpPlmAdapterBase:
    """Thin wrapper base class. Subclasses override `_invoke_*` methods.

    Implements:
    - Public `create_plm` / `close_plm` / `get_documents_list` / `download_file`
      / `upload_file` -- the CorpPlmAdapter Protocol surface
    - Retry-with-backoff for transient failures (Q5)
    - Idempotent `close_plm` (already-closed returns True)

    Subclasses MUST override:
    - `_invoke_create_plm(model_number, tpm_corp_id, title, owner_corp_id, description) -> str | None`
    - `_invoke_close_plm(plm_id, tpm_corp_id) -> bool`
    - `_invoke_get_documents_list(plm_id, tpm_corp_id) -> list[PlmDocumentNode]`
    - `_invoke_download_file(tpm_corp_id, document_id, document_name, file_id, download_path) -> bool`
    - `_invoke_upload_file(plm_id, file_name, file_path) -> bool`
    """

    DEFAULT_MAX_RETRIES = 5
    DEFAULT_BASE_BACKOFF_S = 0.5  # exponential: 0.5, 1.0, 2.0, 4.0, 8.0

    def __init__(
        self,
        customer_id: str,
        max_retries: int | None = None,
        base_backoff_s: float | None = None,
    ) -> None:
        self._customer_id = customer_id
        self._max_retries = max_retries if max_retries is not None else self.DEFAULT_MAX_RETRIES
        self._base_backoff_s = base_backoff_s if base_backoff_s is not None else self.DEFAULT_BASE_BACKOFF_S
        self._closed_plms: set[str] = set()  # idempotence cache for close_plm

    @property
    def source_system(self) -> str:
        return self._customer_id

    # ------------------------------------------------------------------
    # Public Protocol surface (HILDA-side; retries + idempotence + logging)
    # ------------------------------------------------------------------

    async def create_plm(
        self,
        model_number: str,
        tpm_corp_id: str,
        title: str,
        owner_corp_id: str,
        description: str,
    ) -> str | None:
        """Returns plm_id; None on failure per Q5 opaque error."""
        async def _call() -> str | None:
            return await self._invoke_create_plm(
                model_number, tpm_corp_id, title, owner_corp_id, description
            )

        return await self._with_retry(_call, op="create_plm")

    async def close_plm(self, plm_id: str, tpm_corp_id: str) -> bool:
        if plm_id in self._closed_plms:
            return True  # idempotent

        async def _call() -> bool:
            return await self._invoke_close_plm(plm_id, tpm_corp_id)

        result = await self._with_retry(_call, op="close_plm", default_on_exhaust=False)
        if result:
            self._closed_plms.add(plm_id)
        return bool(result)

    async def get_documents_list(
        self, plm_id: str, tpm_corp_id: str
    ) -> list[PlmDocumentNode]:
        async def _call() -> list[PlmDocumentNode]:
            return await self._invoke_get_documents_list(plm_id, tpm_corp_id)

        result = await self._with_retry(_call, op="get_documents_list", default_on_exhaust=[])
        return result or []

    async def download_file(
        self,
        tpm_corp_id: str,
        document_id: str,
        document_name: str,
        file_id: str,
        download_path: Path,
    ) -> bool:
        async def _call() -> bool:
            return await self._invoke_download_file(
                tpm_corp_id, document_id, document_name, file_id, download_path
            )

        return bool(await self._with_retry(_call, op="download_file", default_on_exhaust=False))

    async def upload_file(
        self, plm_id: str, file_name: str, file_path: Path
    ) -> bool:
        async def _call() -> bool:
            return await self._invoke_upload_file(plm_id, file_name, file_path)

        return bool(await self._with_retry(_call, op="upload_file", default_on_exhaust=False))

    # ------------------------------------------------------------------
    # Retry harness (Q5)
    # ------------------------------------------------------------------

    async def _with_retry(
        self,
        op_coro_factory,
        op: str,
        default_on_exhaust=None,
    ):
        """Calls the op coroutine factory up to `_max_retries` times with exponential
        backoff. On final exhaustion: returns `default_on_exhaust` and SHOULD emit
        ITR-W004 (PLM API N-retries-exhausted). Per Q5 architect direction 2026-06-25:
        Ph-1 returns the opaque default + relies on caller / poller to log; the
        explicit OPS alert mechanism is TBD (TODO architect-discussion)."""
        last_exc: Exception | None = None
        for attempt in range(self._max_retries):
            try:
                return await op_coro_factory()
            except NotImplementedError:
                # Surface base-class default loudly -- subclass missing.
                raise PipelineError(
                    "ITR-E001",
                    context={"system": f"corp_plm/{self._customer_id} ({op} not implemented)"},
                )
            except Exception as exc:  # noqa: BLE001 -- opaque per Q5
                last_exc = exc
                if attempt < self._max_retries - 1:
                    await asyncio.sleep(self._base_backoff_s * (2 ** attempt))
                continue
        # All retries exhausted. Per Q5 Ph-1: log ITR-W004 (TODO architect mechanism)
        # and return the default; caller / poller decides next action.
        _ = last_exc  # noqa: F841 -- preserved for future structured logging
        return default_on_exhaust

    # ------------------------------------------------------------------
    # Abstract _invoke_* methods (override in per-customer subclass)
    # ------------------------------------------------------------------

    async def _invoke_create_plm(
        self,
        model_number: str,
        tpm_corp_id: str,
        title: str,
        owner_corp_id: str,
        description: str,
    ) -> str | None:
        raise NotImplementedError(
            "Override in customizations/issue_tracker/<customer_id>_corp_plm_adapter.py per [D-027]"
        )

    async def _invoke_close_plm(self, plm_id: str, tpm_corp_id: str) -> bool:
        raise NotImplementedError(
            "Override in customizations/issue_tracker/<customer_id>_corp_plm_adapter.py per [D-027]"
        )

    async def _invoke_get_documents_list(
        self, plm_id: str, tpm_corp_id: str
    ) -> list[PlmDocumentNode]:
        raise NotImplementedError(
            "Override in customizations/issue_tracker/<customer_id>_corp_plm_adapter.py per [D-027]"
        )

    async def _invoke_download_file(
        self,
        tpm_corp_id: str,
        document_id: str,
        document_name: str,
        file_id: str,
        download_path: Path,
    ) -> bool:
        raise NotImplementedError(
            "Override in customizations/issue_tracker/<customer_id>_corp_plm_adapter.py per [D-027]"
        )

    async def _invoke_upload_file(
        self, plm_id: str, file_name: str, file_path: Path
    ) -> bool:
        raise NotImplementedError(
            "Override in customizations/issue_tracker/<customer_id>_corp_plm_adapter.py per [D-027]"
        )
