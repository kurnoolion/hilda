"""Tests for core/src/sharepoint_integration/sp_writer_impl.py.

Uses a mock SpCrud so we don't need real SharePoint credentials.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from core.src.sharepoint_integration.sp_writer_impl import SpCrudWriter


def test_sp_crud_writer_protocol_surface():
    writer = SpCrudWriter(sp_crud=SimpleNamespace())
    assert hasattr(writer, "update_item")
    assert hasattr(writer, "create_item")


def test_sp_crud_writer_create_item_returns_id_via_sync_bridge():
    mock_crud = SimpleNamespace()
    mock_crud.create_item = AsyncMock(return_value="SP-5001")
    writer = SpCrudWriter(sp_crud=mock_crud)
    scope = SimpleNamespace(customer_id="MMK")
    new_id = writer.create_item(
        entity="delivery_items",
        scope=scope,
        canonical_fields={"item_no": 5, "item_type": "test_tech_waiver_report"},
    )
    assert new_id == "SP-5001"
    mock_crud.create_item.assert_called_once()


def test_sp_crud_writer_update_item_returns_none_via_sync_bridge():
    mock_crud = SimpleNamespace()
    mock_crud.update_item = AsyncMock(return_value=None)
    writer = SpCrudWriter(sp_crud=mock_crud)
    scope = SimpleNamespace(customer_id="MMK")
    result = writer.update_item(
        entity="delivery_items",
        scope=scope,
        item_id="SP-5001",
        canonical_fields={"reminder_count": 2},
    )
    assert result is None
    mock_crud.update_item.assert_called_once()


@pytest.mark.asyncio
async def test_sp_crud_writer_works_from_async_context():
    """Sync wrapper called from an async test should also work via the
    thread-bridged sync helper."""
    mock_crud = SimpleNamespace()
    mock_crud.create_item = AsyncMock(return_value="SP-from-async")
    writer = SpCrudWriter(sp_crud=mock_crud)
    scope = SimpleNamespace(customer_id="MMK")
    new_id = writer.create_item(
        entity="delivery_items", scope=scope,
        canonical_fields={"item_no": 99},
    )
    assert new_id == "SP-from-async"
