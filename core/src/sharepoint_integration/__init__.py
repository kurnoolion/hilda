"""sharepoint_integration — SP REST mechanics + HILDA-entity routing.

See core/src/sharepoint_integration/MODULE.md.
"""
# Register error codes on import.
from core.src.sharepoint_integration import error_codes  # noqa: F401
from core.src.sharepoint_integration.config import (
    GlobalSharePointConfig,
    ListScope,
)
from core.src.sharepoint_integration.list_crud import SpCrud
from core.src.sharepoint_integration.list_provider import (
    FileBasedListProvider,
    SharePointListProvider,
)
from core.src.sharepoint_integration.sp_client import SpClient

__all__ = [
    "FileBasedListProvider",
    "GlobalSharePointConfig",
    "ListScope",
    "SharePointListProvider",
    "SpClient",
    "SpCrud",
]
