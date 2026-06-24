"""Unit tests for mock SP server (REST + UI)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from core.src.sharepoint_integration import (
    FileBasedListProvider,
    GlobalSharePointConfig,
    ListScope,
    SpClient,
    SpCrud,
)
from core.src.sharepoint_integration.mock_server import build_app


@pytest.fixture
def client() -> TestClient:
    return TestClient(build_app())


# --- REST surface -----------------------------------------------------------


class TestRestEndpoints:
    def test_health_starts_empty(self, client: TestClient) -> None:
        r = client.get("/health")
        assert r.status_code == 200
        assert r.json() == {"ok": True, "lists": 0, "audit_entries": 0}

    def test_create_get_round_trip(self, client: TestClient) -> None:
        r = client.post(
            "/_api/web/lists/getbytitle('CA-Delivery')/items",
            json={"Title": "Band-1", "Owner": "rd@corp.com"},
        )
        assert r.status_code == 201
        body = r.json()
        assert body["Id"] == 1
        r2 = client.get("/_api/web/lists/getbytitle('CA-Delivery')/items")
        assert r2.status_code == 200
        items = r2.json()["value"]
        assert items == [{"Id": 1, "Title": "Band-1", "Owner": "rd@corp.com"}]

    def test_list_not_found_returns_404(self, client: TestClient) -> None:
        r = client.get("/_api/web/lists/getbytitle('Nope')/items")
        assert r.status_code == 404
        assert r.json()["error"]["code"].startswith("-1")

    def test_list_with_apostrophe(self, client: TestClient) -> None:
        # Quote escape: ' -> ''  (per SP REST convention)
        r = client.post(
            "/_api/web/lists/getbytitle('PM''s Items')/items",
            json={"Title": "x"},
        )
        assert r.status_code == 201
        r2 = client.get("/_api/web/lists/getbytitle('PM''s Items')/items")
        assert r2.json()["value"][0]["Title"] == "x"

    def test_patch_updates_in_place(self, client: TestClient) -> None:
        client.post("/_api/web/lists/getbytitle('L')/items", json={"Title": "v1"})
        r = client.patch(
            "/_api/web/lists/getbytitle('L')/items(1)",
            json={"Title": "v2"},
        )
        assert r.status_code == 204
        items = client.get("/_api/web/lists/getbytitle('L')/items").json()["value"]
        assert items[0]["Title"] == "v2"

    def test_delete_removes(self, client: TestClient) -> None:
        client.post("/_api/web/lists/getbytitle('L')/items", json={"Title": "x"})
        r = client.delete("/_api/web/lists/getbytitle('L')/items(1)")
        assert r.status_code == 204
        items = client.get("/_api/web/lists/getbytitle('L')/items").json()["value"]
        assert items == []

    def test_filter_eq_string(self, client: TestClient) -> None:
        client.post("/_api/web/lists/getbytitle('L')/items", json={"Status": "Open"})
        client.post("/_api/web/lists/getbytitle('L')/items", json={"Status": "Closed"})
        r = client.get(
            "/_api/web/lists/getbytitle('L')/items",
            params={"$filter": "Status eq 'Open'"},
        )
        items = r.json()["value"]
        assert len(items) == 1
        assert items[0]["Status"] == "Open"

    def test_select_filters_columns(self, client: TestClient) -> None:
        client.post(
            "/_api/web/lists/getbytitle('L')/items",
            json={"Title": "x", "Owner": "y", "Status": "Open"},
        )
        r = client.get(
            "/_api/web/lists/getbytitle('L')/items",
            params={"$select": "Title,Status"},
        )
        item = r.json()["value"][0]
        assert "Owner" not in item
        assert item["Title"] == "x"
        assert item["Status"] == "Open"

    def test_top_limits_results(self, client: TestClient) -> None:
        for i in range(5):
            client.post("/_api/web/lists/getbytitle('L')/items", json={"i": i})
        r = client.get(
            "/_api/web/lists/getbytitle('L')/items",
            params={"$top": "2"},
        )
        assert len(r.json()["value"]) == 2


# --- UI surface -------------------------------------------------------------


class TestUI:
    def test_index_renders_html(self, client: TestClient) -> None:
        r = client.get("/")
        assert r.status_code == 200
        assert "text/html" in r.headers["content-type"]
        assert "Mock SharePoint" in r.text

    def test_index_lists_after_create(self, client: TestClient) -> None:
        client.post("/_api/web/lists/getbytitle('CA-Delivery')/items", json={"x": 1})
        r = client.get("/")
        assert "CA-Delivery" in r.text

    def test_list_detail_shows_items(self, client: TestClient) -> None:
        client.post(
            "/_api/web/lists/getbytitle('CA-Delivery')/items",
            json={"Title": "Band-1"},
        )
        r = client.get("/lists/CA-Delivery")
        assert r.status_code == 200
        assert "Band-1" in r.text
        assert "Title" in r.text

    def test_audit_page_shows_operations(self, client: TestClient) -> None:
        client.post("/_api/web/lists/getbytitle('L')/items", json={"x": 1})
        r = client.get("/audit")
        assert "POST" in r.text
        assert "L" in r.text

    def test_html_escapes_user_content(self, client: TestClient) -> None:
        # Defensive — even though list names come from trusted callers, the UI
        # must escape (TPM may interact via curl/console with weird names).
        client.post(
            "/_api/web/lists/getbytitle('NoXSS')/items",
            json={"Title": "<script>alert(1)</script>"},
        )
        r = client.get("/lists/NoXSS")
        assert "<script>" not in r.text  # raw tag should be escaped
        assert "&lt;script&gt;" in r.text


# --- End-to-end: SpCrud → mock server -------------------------------------


class TestSpCrudAgainstMockServer:
    @pytest.mark.asyncio
    async def test_full_round_trip(self, tmp_path: Path) -> None:
        # Set up a customer config
        customers = tmp_path / "customers"
        customers.mkdir()
        (customers / "test_customer.yaml").write_text(
            json.dumps(
                {
                    "customer_id": "test_customer",
                    "lists": {
                        "delivery_items": {
                            "name": "Deliverables_test_customer",
                            "columns": {
                                "item_name": "Title",
                                "owner_corp_email": "Owner_x0020_Corp_x0020_Email",
                                "delivery_state": "Delivery_x0020_State",
                            },
                        },
                    },
                }
            )
        )

        # Build mock server + ASGI transport
        app = build_app()
        import httpx
        transport = httpx.ASGITransport(app=app)

        cfg = GlobalSharePointConfig(
            site_url="http://mock-sp", auth_type="none", page_size=10
        )
        client = SpClient(cfg, transport=transport)
        provider = FileBasedListProvider(tmp_path)
        crud = SpCrud(client, provider)

        async with client:
            # Create
            item_id = await crud.create_item(
                "delivery_items",
                ListScope("test_customer"),
                {
                    "item_name": "Band-1",
                    "owner_corp_email": "rd@corp.com",
                    "delivery_state": "Open",
                },
            )
            assert item_id == "1"

            # Read all
            items = await crud.get_items("delivery_items", ListScope("test_customer"))
            assert len(items) == 1
            assert items[0]["item_name"] == "Band-1"
            assert items[0]["owner_corp_email"] == "rd@corp.com"

            # Filter
            items_filtered = await crud.get_items(
                "delivery_items",
                ListScope("test_customer"),
                canonical_filters={"delivery_state": "Open"},
            )
            assert len(items_filtered) == 1

            # Update
            await crud.update_item(
                "delivery_items",
                ListScope("test_customer"),
                item_id,
                {"delivery_state": "Closed"},
            )
            items_after = await crud.get_items(
                "delivery_items", ListScope("test_customer")
            )
            assert items_after[0]["delivery_state"] == "Closed"

            # Delete
            await crud.delete_item(
                "delivery_items", ListScope("test_customer"), item_id
            )
            items_empty = await crud.get_items(
                "delivery_items", ListScope("test_customer")
            )
            assert items_empty == []
