"""Example per-customer corp PLM adapter scaffold.

Per Module #13 cascade D2 + `[D-027]` Teacher/Student split 2026-06-25.

COPY this file to `<customer_id>_corp_plm_adapter.py` (e.g.,
`test_carrier_a_corp_plm_adapter.py`), then fill in the `# TODO(cline)` markers
with the actual corp PLM binding call body. The 5 binding API methods are
already available in the corp environment per architect spec 2026-06-25:

    str  createPLM(Model_Number, tpm_corp_id, title, owner_corp_id, description)
                                                            -> plm_id, None on failure
    bool closePLM(plm_id, tpm_corp_id)                      -> True on success
    list getdocumentslist(plm_id, tpm_corp_id)              -> list of {document_ID, document_name, fileID}
    bool downloadFile(tpm_corp_id, document_ID, document_name, fileID, downloadPath)
                                                            -> True on success
    bool uploadFile(plm_id, file_name, file_path)           -> True on success

NEVER lands on public github per NFR-2 -- per-customer subclass is the
proprietary boundary.
"""
from __future__ import annotations

from pathlib import Path

from core.src.issue_tracker.corp_plm.adapter import CorpPlmAdapterBase
from core.src.issue_tracker.protocol import PlmDocumentNode


# TODO(cline): import the actual corp PLM binding module from the Work PC env
# e.g.,
# from corp_plm_gateway_client import (
#     createPLM, closePLM, getdocumentslist, downloadFile, uploadFile,
# )


class ExampleCorpPlmAdapter(CorpPlmAdapterBase):
    """Per-customer corp PLM adapter scaffold.

    Subclass instances are constructed per customer_id; the binding may share
    state across customers (multi-tenant PLM) or be per-customer (single-tenant).
    """

    def __init__(self, customer_id: str = "example") -> None:
        super().__init__(customer_id=customer_id)

    async def _invoke_create_plm(
        self,
        model_number: str,
        tpm_corp_id: str,
        title: str,
        owner_corp_id: str,
        description: str,
    ) -> str | None:
        # TODO(cline): call the corp PLM binding
        # return createPLM(model_number, tpm_corp_id, title, owner_corp_id, description)
        raise NotImplementedError("TODO(cline): wire corp PLM createPLM binding here")

    async def _invoke_close_plm(self, plm_id: str, tpm_corp_id: str) -> bool:
        # TODO(cline): return closePLM(plm_id, tpm_corp_id)
        raise NotImplementedError("TODO(cline): wire corp PLM closePLM binding here")

    async def _invoke_get_documents_list(
        self, plm_id: str, tpm_corp_id: str
    ) -> list[PlmDocumentNode]:
        # TODO(cline):
        # raw = getdocumentslist(plm_id, tpm_corp_id)
        # return [PlmDocumentNode(document_id=n["document_ID"],
        #                         document_name=n["document_name"],
        #                         file_id=n["fileID"]) for n in raw]
        raise NotImplementedError("TODO(cline): wire corp PLM getdocumentslist binding here")

    async def _invoke_download_file(
        self,
        tpm_corp_id: str,
        document_id: str,
        document_name: str,
        file_id: str,
        download_path: Path,
    ) -> bool:
        # TODO(cline):
        # download_path.parent.mkdir(parents=True, exist_ok=True)
        # return downloadFile(tpm_corp_id, document_id, document_name, file_id, str(download_path))
        raise NotImplementedError("TODO(cline): wire corp PLM downloadFile binding here")

    async def _invoke_upload_file(
        self, plm_id: str, file_name: str, file_path: Path
    ) -> bool:
        # TODO(cline): return uploadFile(plm_id, file_name, str(file_path))
        raise NotImplementedError("TODO(cline): wire corp PLM uploadFile binding here")


def make_corp_plm_adapter(customer_id: str = "example") -> ExampleCorpPlmAdapter:
    """Factory for load_adapter() / CLI --plm-diagnostic mode."""
    return ExampleCorpPlmAdapter(customer_id=customer_id)
