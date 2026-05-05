"""Mock SharePoint server — REST API stub + web UI.

Two surfaces over the same in-memory state:
  - REST: SP 2017 endpoints under /_api/web/lists/getbytitle('X')/items
  - Web UI: HTML browser at / for visual inspection by TPMs

Designed for extension: the InMemoryStore is pluggable so future modules
(dashboard mock) can plug their own routes onto the same FastAPI app.
"""
from core.src.sharepoint_integration.mock_server.app import build_app
from core.src.sharepoint_integration.mock_server.store import InMemoryStore, ListNotFoundError

__all__ = ["InMemoryStore", "ListNotFoundError", "build_app"]
