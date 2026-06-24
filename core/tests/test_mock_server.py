"""Unit tests for mock SP server (REST + UI + architect digest-dance)."""
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
    list_item_type,
)
from core.src.sharepoint_integration.mock_server import MockSpSession, build_app


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
            json={
                "__metadata": {"type": list_item_type("CA-Delivery")},
                "Title": "Band-1",
                "Owner": "rd@corp.com",
            },
            headers={"X-RequestDigest": "stub"},
        )
        assert r.status_code == 201
        body = r.json()["d"]
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
        r = client.post(
            "/_api/web/lists/getbytitle('PM''s Items')/items",
            json={"__metadata": {"type": "x"}, "Title": "x"},
            headers={"X-RequestDigest": "stub"},
        )
        assert r.status_code == 201
        r2 = client.get("/_api/web/lists/getbytitle('PM''s Items')/items")
        assert r2.json()["value"][0]["Title"] == "x"

    def test_patch_updates_in_place(self, client: TestClient) -> None:
        client.post(
            "/_api/web/lists/getbytitle('L')/items",
            json={"Title": "v1"},
            headers={"X-RequestDigest": "stub"},
        )
        r = client.patch(
            "/_api/web/lists/getbytitle('L')/items(1)",
            json={"Title": "v2"},
        )
        assert r.status_code == 204
        items = client.get("/_api/web/lists/getbytitle('L')/items").json()["value"]
        assert items[0]["Title"] == "v2"

    def test_delete_removes(self, client: TestClient) -> None:
        client.post(
            "/_api/web/lists/getbytitle('L')/items",
            json={"Title": "x"},
            headers={"X-RequestDigest": "stub"},
        )
        r = client.delete("/_api/web/lists/getbytitle('L')/items(1)")
        assert r.status_code == 204
        items = client.get("/_api/web/lists/getbytitle('L')/items").json()["value"]
        assert items == []

    def test_filter_eq_string(self, client: TestClient) -> None:
        client.post(
            "/_api/web/lists/getbytitle('L')/items",
            json={"Status": "Open"},
            headers={"X-RequestDigest": "stub"},
        )
        client.post(
            "/_api/web/lists/getbytitle('L')/items",
            json={"Status": "Closed"},
            headers={"X-RequestDigest": "stub"},
        )
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
            headers={"X-RequestDigest": "stub"},
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
            client.post(
                "/_api/web/lists/getbytitle('L')/items",
                json={"i": i},
                headers={"X-RequestDigest": "stub"},
            )
        r = client.get(
            "/_api/web/lists/getbytitle('L')/items",
            params={"$top": "2"},
        )
        assert len(r.json()["value"]) == 2


# --- Architect digest-dance endpoints ---------------------------------------


class TestDigestDance:
    def test_get_site_root_sets_wssauth_cookie(self, client: TestClient) -> None:
        r = client.get("/", headers={"Accept": "application/json;odata=verbose"})
        assert r.status_code == 200
        assert "WSSAUTH" in r.headers.get("set-cookie", "")

    def test_contextinfo_returns_form_digest_value(self, client: TestClient) -> None:
        r = client.post("/_api/contextinfo")
        assert r.status_code == 200
        body = r.json()
        assert "FormDigestValue" in body["d"]["GetContextWebInformation"]
        assert body["d"]["GetContextWebInformation"]["FormDigestValue"].startswith(
            "mock-digest-1"
        )

    def test_contextinfo_emits_fresh_token_each_call(self, client: TestClient) -> None:
        v1 = client.post("/_api/contextinfo").json()["d"]["GetContextWebInformation"][
            "FormDigestValue"
        ]
        v2 = client.post("/_api/contextinfo").json()["d"]["GetContextWebInformation"][
            "FormDigestValue"
        ]
        assert v1 != v2

    def test_write_without_digest_returns_403(self, client: TestClient) -> None:
        r = client.post(
            "/_api/web/lists/getbytitle('L')/items",
            json={"__metadata": {"type": "x"}, "Title": "x"},
        )
        assert r.status_code == 403
        assert r.json()["error"]["code"].startswith("-2130575251")

    def test_merge_via_x_http_method(self, client: TestClient) -> None:
        # Create first
        client.post(
            "/_api/web/lists/getbytitle('L')/items",
            json={"Title": "v1"},
            headers={"X-RequestDigest": "stub"},
        )
        # MERGE via pseudo-verb
        r = client.post(
            "/_api/web/lists/getbytitle('L')/items(1)",
            json={"__metadata": {"type": list_item_type("L")}, "Title": "v2"},
            headers={
                "X-RequestDigest": "stub",
                "X-Http-Method": "MERGE",
                "IF-MATCH": "*",
            },
        )
        assert r.status_code == 204
        items = client.get("/_api/web/lists/getbytitle('L')/items").json()["value"]
        assert items[0]["Title"] == "v2"

    def test_delete_via_x_http_method(self, client: TestClient) -> None:
        client.post(
            "/_api/web/lists/getbytitle('L')/items",
            json={"Title": "v1"},
            headers={"X-RequestDigest": "stub"},
        )
        r = client.post(
            "/_api/web/lists/getbytitle('L')/items(1)",
            headers={
                "X-RequestDigest": "stub",
                "X-Http-Method": "DELETE",
                "IF-MATCH": "*",
            },
            json={},
        )
        assert r.status_code == 204
        items = client.get("/_api/web/lists/getbytitle('L')/items").json()["value"]
        assert items == []

    def test_force_403_consumes_one_write(self, client: TestClient) -> None:
        client.post("/__force_403_next_writes__", params={"n": 1})
        r1 = client.post(
            "/_api/web/lists/getbytitle('L')/items",
            json={"Title": "x"},
            headers={"X-RequestDigest": "stub"},
        )
        assert r1.status_code == 403
        # Next call should succeed
        r2 = client.post(
            "/_api/web/lists/getbytitle('L')/items",
            json={"Title": "x"},
            headers={"X-RequestDigest": "stub"},
        )
        assert r2.status_code == 201


# --- UI surface -------------------------------------------------------------


class TestUI:
    def test_index_renders_html(self, client: TestClient) -> None:
        r = client.get("/")
        assert r.status_code == 200
        assert "text/html" in r.headers["content-type"]
        assert "Mock SharePoint" in r.text

    def test_index_lists_after_create(self, client: TestClient) -> None:
        client.post(
            "/_api/web/lists/getbytitle('CA-Delivery')/items",
            json={"x": 1},
            headers={"X-RequestDigest": "stub"},
        )
        r = client.get("/")
        assert "CA-Delivery" in r.text

    def test_list_detail_shows_items(self, client: TestClient) -> None:
        client.post(
            "/_api/web/lists/getbytitle('CA-Delivery')/items",
            json={"Title": "Band-1"},
            headers={"X-RequestDigest": "stub"},
        )
        r = client.get("/lists/CA-Delivery")
        assert r.status_code == 200
        assert "Band-1" in r.text
        assert "Title" in r.text

    def test_audit_page_shows_operations(self, client: TestClient) -> None:
        client.post(
            "/_api/web/lists/getbytitle('L')/items",
            json={"x": 1},
            headers={"X-RequestDigest": "stub"},
        )
        r = client.get("/audit")
        assert "POST" in r.text
        assert "L" in r.text

    def test_html_escapes_user_content(self, client: TestClient) -> None:
        client.post(
            "/_api/web/lists/getbytitle('NoXSS')/items",
            json={"Title": "<script>alert(1)</script>"},
            headers={"X-RequestDigest": "stub"},
        )
        r = client.get("/lists/NoXSS")
        assert "<script>" not in r.text
        assert "&lt;script&gt;" in r.text


# --- End-to-end: SpCrud → MockSpSession → mock server ----------------------


class TestSpCrudAgainstMockServer:
    @pytest.mark.asyncio
    async def test_full_round_trip(self, tmp_path: Path) -> None:
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

        app = build_app()
        session = MockSpSession(app)

        cfg = GlobalSharePointConfig(
            site_url="http://mock-sp", auth_type="none", page_size=10
        )
        client = SpClient(cfg, session=session)
        provider = FileBasedListProvider(tmp_path)
        crud = SpCrud(client, provider)

        async with client:
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

            items = await crud.get_items("delivery_items", ListScope("test_customer"))
            assert len(items) == 1
            assert items[0]["item_name"] == "Band-1"
            assert items[0]["owner_corp_email"] == "rd@corp.com"

            items_filtered = await crud.get_items(
                "delivery_items",
                ListScope("test_customer"),
                canonical_filters={"delivery_state": "Open"},
            )
            assert len(items_filtered) == 1

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

            await crud.delete_item(
                "delivery_items", ListScope("test_customer"), item_id
            )
            items_empty = await crud.get_items(
                "delivery_items", ListScope("test_customer")
            )
            assert items_empty == []

    @pytest.mark.asyncio
    async def test_refresh_on_403_path_through_spclient(self, tmp_path: Path) -> None:
        """Architect Q2 lock 2026-06-25: on 403 from a write, SpSession
        re-runs the digest dance and retries once. End-to-end through SpCrud."""
        customers = tmp_path / "customers"
        customers.mkdir()
        (customers / "test_customer.yaml").write_text(
            json.dumps(
                {
                    "customer_id": "test_customer",
                    "lists": {
                        "delivery_items": {
                            "name": "Deliverables_test_customer",
                            "columns": {"item_name": "Title"},
                        },
                    },
                }
            )
        )

        app = build_app()
        session = MockSpSession(app)
        cfg = GlobalSharePointConfig(site_url="http://mock-sp", auth_type="none")
        client = SpClient(cfg, session=session)
        provider = FileBasedListProvider(tmp_path)
        crud = SpCrud(client, provider)

        # First, do an initial successful create so we have item_id 1 to update.
        async with client:
            item_id = await crud.create_item(
                "delivery_items",
                ListScope("test_customer"),
                {"item_name": "v1"},
            )
            # Now queue a 403 for the next write.
            app.state.store.digest_403_count = 1
            digest_before = session._digest  # type: ignore[attr-defined]
            await crud.update_item(
                "delivery_items",
                ListScope("test_customer"),
                item_id,
                {"item_name": "v2"},
            )
            digest_after = session._digest  # type: ignore[attr-defined]
            # The session should have refreshed its digest (new value)
            assert digest_before != digest_after
            items = await crud.get_items(
                "delivery_items", ListScope("test_customer")
            )
            assert items[0]["item_name"] == "v2"
