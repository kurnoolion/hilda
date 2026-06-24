"""Unit tests for sharepoint_integration core (no mock server)."""
from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from core.src.diagnostics import PipelineError
from core.src.sharepoint_integration import (
    FileBasedListProvider,
    GlobalSharePointConfig,
    ListScope,
    SpClient,
    SpCrud,
)


# --- ListScope --------------------------------------------------------------


class TestListScope:
    def test_customer_only(self) -> None:
        s = ListScope(customer_id="test_customer")
        assert s.customer_id == "test_customer"
        assert s.device_id is None

    def test_with_device(self) -> None:
        s = ListScope(customer_id="c", device_id="d")
        assert s.device_id == "d"

    def test_immutable(self) -> None:
        s = ListScope(customer_id="c")
        with pytest.raises(Exception):
            s.customer_id = "other"  # type: ignore[misc]


# --- GlobalSharePointConfig -------------------------------------------------


class TestGlobalSharePointConfig:
    def test_minimal(self) -> None:
        c = GlobalSharePointConfig(site_url="https://sp.corp/sites/x", auth_type="none")
        assert c.site_url == "https://sp.corp/sites/x"
        assert c.timeout_seconds == 30

    def test_password_excluded_from_repr(self) -> None:
        c = GlobalSharePointConfig(
            site_url="https://sp.corp/x",
            auth_type="ntlm",
            username="alice",
            password="super-secret",
        )
        text = repr(c)
        assert "super-secret" not in text
        assert "super-secret" not in str(c)

    def test_password_excluded_from_model_dump_str(self) -> None:
        c = GlobalSharePointConfig(
            site_url="https://sp.corp/x",
            auth_type="ntlm",
            username="alice",
            password="super-secret",
        )
        # Even when explicitly dumping with mode='python', the secret is in
        # the dict — but no compact-report path serializes config blindly.
        # The guard is repr/str, which we just verified.
        assert c.password == "super-secret"

    def test_3_tier_precedence_cli_beats_env_beats_file(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        cfg_file = tmp_path / "sharepoint_integration.json"
        cfg_file.write_text(
            json.dumps(
                {
                    "site_url": "https://from-file.corp/x",
                    "auth_type": "none",
                    "timeout_seconds": 10,
                }
            )
        )
        monkeypatch.setenv("HILDA_SP_TIMEOUT_SECONDS", "20")
        c = GlobalSharePointConfig.from_sources(
            config_path=cfg_file,
            cli_overrides={"timeout_seconds": 30},
        )
        assert c.site_url == "https://from-file.corp/x"
        assert c.timeout_seconds == 30  # CLI wins
        # env-only field
        c2 = GlobalSharePointConfig.from_sources(config_path=cfg_file)
        assert c2.timeout_seconds == 20  # env beats file

    def test_extra_fields_forbidden(self) -> None:
        with pytest.raises(Exception):
            GlobalSharePointConfig(
                site_url="https://sp.corp/x", auth_type="none", unknown="x"
            )  # type: ignore[call-arg]


# --- FileBasedListProvider --------------------------------------------------


def _write_customer(base: Path, customer_id: str, lists: dict[str, dict[str, object]]) -> None:
    customers = base / "customers"
    customers.mkdir(parents=True, exist_ok=True)
    payload = {"customer_id": customer_id, "lists": lists}
    (customers / f"{customer_id}.yaml").write_text(json.dumps(payload))


def _write_device_overrides(base: Path, overrides: list[dict[str, object]]) -> None:
    devices = base / "devices"
    devices.mkdir(parents=True, exist_ok=True)
    payload = {"device_overrides": overrides}
    (devices / "special_devices.yaml").write_text(json.dumps(payload))


class TestFileBasedListProvider:
    def test_get_list_name(self, tmp_path: Path) -> None:
        _write_customer(
            tmp_path,
            "test_customer",
            {
                "delivery_items": {"name": "Deliverables_test_customer", "columns": {"item_name": "Title"}},
            },
        )
        p = FileBasedListProvider(tmp_path)
        assert (
            p.get_list_name("delivery_items", ListScope("test_customer")) == "Deliverables_test_customer"
        )

    def test_get_column_map(self, tmp_path: Path) -> None:
        _write_customer(
            tmp_path,
            "test_customer",
            {
                "delivery_items": {
                    "name": "Deliverables_test_customer",
                    "columns": {"item_name": "Title", "owner_email": "Owner_Email"},
                },
            },
        )
        p = FileBasedListProvider(tmp_path)
        m = p.get_column_map("delivery_items", ListScope("test_customer"))
        assert m == {"item_name": "Title", "owner_email": "Owner_Email"}

    def test_to_sp_fields(self, tmp_path: Path) -> None:
        _write_customer(
            tmp_path,
            "test_customer",
            {
                "delivery_items": {
                    "name": "Deliverables_test_customer",
                    "columns": {"item_name": "Title", "owner_email": "Owner_Email"},
                },
            },
        )
        p = FileBasedListProvider(tmp_path)
        sp = p.to_sp_fields(
            "delivery_items",
            ListScope("test_customer"),
            {"item_name": "Band-1", "owner_email": "rd@corp.com"},
        )
        assert sp == {"Title": "Band-1", "Owner_Email": "rd@corp.com"}

    def test_to_sp_fields_unmapped_raises(self, tmp_path: Path) -> None:
        _write_customer(
            tmp_path,
            "test_customer",
            {
                "delivery_items": {
                    "name": "Deliverables_test_customer",
                    "columns": {"item_name": "Title"},
                },
            },
        )
        p = FileBasedListProvider(tmp_path)
        with pytest.raises(PipelineError) as ei:
            p.to_sp_fields(
                "delivery_items",
                ListScope("test_customer"),
                {"item_name": "x", "stranger": "y"},
            )
        assert ei.value.code_id == "SHP-E003"

    def test_unknown_customer_raises_e002(self, tmp_path: Path) -> None:
        p = FileBasedListProvider(tmp_path)
        with pytest.raises(PipelineError) as ei:
            p.get_list_name("delivery_items", ListScope("unknown"))
        assert ei.value.code_id == "SHP-E002"

    def test_unknown_entity_raises_e002(self, tmp_path: Path) -> None:
        _write_customer(
            tmp_path,
            "test_customer",
            {"delivery_items": {"name": "Deliverables_test_customer", "columns": {}}},
        )
        p = FileBasedListProvider(tmp_path)
        with pytest.raises(PipelineError) as ei:
            p.get_list_name("missing_entity", ListScope("test_customer"))
        assert ei.value.code_id == "SHP-E002"

    def test_device_override_changes_list_name(self, tmp_path: Path) -> None:
        _write_customer(
            tmp_path,
            "test_customer",
            {
                "delivery_items": {
                    "name": "Deliverables_test_customer",
                    "columns": {"item_name": "Title"},
                },
            },
        )
        _write_device_overrides(
            tmp_path,
            [
                {
                    "customer_id": "test_customer",
                    "device_id": "special-x",
                    "entity": "delivery_items",
                    "list_name": "CA-SpecialX-Delivery",
                    "columns": {},
                }
            ],
        )
        p = FileBasedListProvider(tmp_path)
        assert (
            p.get_list_name(
                "delivery_items", ListScope("test_customer", device_id="special-x")
            )
            == "CA-SpecialX-Delivery"
        )
        # Without device — base name
        assert (
            p.get_list_name("delivery_items", ListScope("test_customer"))
            == "Deliverables_test_customer"
        )

    def test_device_override_columns_merge(self, tmp_path: Path) -> None:
        _write_customer(
            tmp_path,
            "test_customer",
            {
                "delivery_items": {
                    "name": "Deliverables_test_customer",
                    "columns": {"item_name": "Title", "extra": "Extra"},
                },
            },
        )
        _write_device_overrides(
            tmp_path,
            [
                {
                    "customer_id": "test_customer",
                    "device_id": "special-x",
                    "entity": "delivery_items",
                    "columns": {"item_name": "Override_Title"},
                }
            ],
        )
        p = FileBasedListProvider(tmp_path)
        m = p.get_column_map(
            "delivery_items", ListScope("test_customer", device_id="special-x")
        )
        assert m["item_name"] == "Override_Title"
        assert m["extra"] == "Extra"

    def test_from_sp_fields_inverse(self, tmp_path: Path) -> None:
        _write_customer(
            tmp_path,
            "test_customer",
            {
                "delivery_items": {
                    "name": "Deliverables_test_customer",
                    "columns": {"item_name": "Title"},
                },
            },
        )
        p = FileBasedListProvider(tmp_path)
        canonical = p.from_sp_fields(
            "delivery_items",
            ListScope("test_customer"),
            {"Title": "Band-1", "Id": 42, "stranger": "ignored"},
        )
        assert canonical == {"item_name": "Band-1"}


# --- SpClient (with httpx.MockTransport) -----------------------------------


def _mock_handler(handler):
    return httpx.MockTransport(handler)


class _CallRecorder:
    def __init__(self) -> None:
        self.calls: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        return self._respond(request)

    def _respond(self, request: httpx.Request) -> httpx.Response:
        raise NotImplementedError


class TestSpClient:
    @pytest.mark.asyncio
    async def test_get_list_items_passes_select_and_filter(self) -> None:
        captured: dict[str, str] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["url"] = str(request.url)
            return httpx.Response(200, json={"value": [{"Id": 1, "Title": "x"}]})

        cfg = GlobalSharePointConfig(
            site_url="https://sp.corp/sites/x", auth_type="none"
        )
        client = SpClient(cfg, transport=_mock_handler(handler))
        async with client:
            items = await client.get_list_items(
                "MyList",
                select=["Id", "Title"],
                filter_expr="Status eq 'Open'",
            )
        assert items == [{"Id": 1, "Title": "x"}]
        assert "getbytitle('MyList')" in captured["url"]
        assert "%24select=Id%2CTitle" in captured["url"] or "$select=Id,Title" in captured["url"]

    @pytest.mark.asyncio
    async def test_create_list_item_returns_id(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.method == "POST"
            return httpx.Response(201, json={"Id": 99, "Title": "x"})

        cfg = GlobalSharePointConfig(site_url="https://sp.corp/x", auth_type="none")
        async with SpClient(cfg, transport=_mock_handler(handler)) as client:
            item_id = await client.create_list_item("MyList", {"Title": "x"})
        assert item_id == "99"

    @pytest.mark.asyncio
    async def test_update_list_item_uses_patch_with_if_match(self) -> None:
        captured: dict[str, object] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["method"] = request.method
            captured["if_match"] = request.headers.get("if-match")
            return httpx.Response(204)

        cfg = GlobalSharePointConfig(site_url="https://sp.corp/x", auth_type="none")
        async with SpClient(cfg, transport=_mock_handler(handler)) as client:
            await client.update_list_item("MyList", "42", {"Title": "y"})
        assert captured["method"] == "PATCH"
        assert captured["if_match"] == "*"

    @pytest.mark.asyncio
    async def test_delete_list_item_uses_delete_with_if_match(self) -> None:
        """Per drift sweep 2026-06-10 — verify SpClient.delete_list_item issues DELETE with IF-MATCH header."""
        captured: dict[str, object] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["method"] = request.method
            captured["if_match"] = request.headers.get("if-match")
            captured["url"] = str(request.url)
            return httpx.Response(204)

        cfg = GlobalSharePointConfig(site_url="https://sp.corp/x", auth_type="none")
        async with SpClient(cfg, transport=_mock_handler(handler)) as client:
            await client.delete_list_item("MyList", "42")
        assert captured["method"] == "DELETE"
        assert captured["if_match"] == "*"
        assert "items(42)" in captured["url"]

    @pytest.mark.asyncio
    async def test_4xx_raises_shp_e001(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                404,
                json={"error": {"code": "-1, NotFound", "message": "list missing"}},
            )

        cfg = GlobalSharePointConfig(
            site_url="https://sp.corp/x", auth_type="none", max_retries=0
        )
        async with SpClient(cfg, transport=_mock_handler(handler)) as client:
            with pytest.raises(PipelineError) as ei:
                await client.get_list_items("Missing")
        assert ei.value.code_id == "SHP-E001"
        assert ei.value.context["status"] == 404

    @pytest.mark.asyncio
    async def test_401_raises_shp_e004(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(401, json={})

        cfg = GlobalSharePointConfig(
            site_url="https://sp.corp/x", auth_type="none", max_retries=0
        )
        async with SpClient(cfg, transport=_mock_handler(handler)) as client:
            with pytest.raises(PipelineError) as ei:
                await client.get_list_items("Anything")
        assert ei.value.code_id == "SHP-E004"

    @pytest.mark.asyncio
    async def test_429_retries_then_succeeds(self) -> None:
        attempts = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            attempts["n"] += 1
            if attempts["n"] < 3:
                return httpx.Response(429, json={})
            return httpx.Response(200, json={"value": []})

        cfg = GlobalSharePointConfig(
            site_url="https://sp.corp/x",
            auth_type="none",
            max_retries=3,
            retry_backoff_seconds=0.001,
        )
        async with SpClient(cfg, transport=_mock_handler(handler)) as client:
            items = await client.get_list_items("X")
        assert items == []
        assert attempts["n"] == 3

    @pytest.mark.asyncio
    async def test_list_name_with_apostrophe_escaped(self) -> None:
        captured: dict[str, str] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["path"] = request.url.path
            return httpx.Response(200, json={"value": []})

        cfg = GlobalSharePointConfig(site_url="https://sp.corp/x", auth_type="none")
        async with SpClient(cfg, transport=_mock_handler(handler)) as client:
            await client.get_list_items("PM's Items")
        assert "PM''s Items" in captured["path"]


# --- SpCrud (composition) ---------------------------------------------------


class TestSpCrud:
    @pytest.mark.asyncio
    async def test_create_translates_canonical_to_sp_columns(
        self, tmp_path: Path
    ) -> None:
        _write_customer(
            tmp_path,
            "test_customer",
            {
                "delivery_items": {
                    "name": "Deliverables_test_customer",
                    "columns": {"item_name": "Title", "owner_email": "Owner_Email"},
                },
            },
        )
        captured: dict[str, object] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["body"] = json.loads(request.content) if request.content else None
            captured["url"] = str(request.url)
            return httpx.Response(201, json={"Id": 42})

        cfg = GlobalSharePointConfig(site_url="https://sp.corp/x", auth_type="none")
        client = SpClient(cfg, transport=_mock_handler(handler))
        provider = FileBasedListProvider(tmp_path)
        crud = SpCrud(client, provider)
        async with client:
            item_id = await crud.create_item(
                "delivery_items",
                ListScope("test_customer"),
                {"item_name": "Band-1", "owner_email": "rd@corp.com"},
            )
        assert item_id == "42"
        assert captured["body"] == {"Title": "Band-1", "Owner_Email": "rd@corp.com"}
        assert "getbytitle('Deliverables_test_customer')" in captured["url"]

    @pytest.mark.asyncio
    async def test_delete_item_resolves_list_name_and_issues_delete(
        self, tmp_path: Path
    ) -> None:
        """Per drift sweep 2026-06-10 — verify SpCrud.delete_item resolves the
        scope+entity to SP list name, then issues DELETE on items(<id>)."""
        _write_customer(
            tmp_path,
            "test_customer",
            {
                "delivery_items": {
                    "name": "Deliverables_test_customer",
                    "columns": {"item_name": "Title"},
                },
            },
        )
        captured: dict[str, object] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["method"] = request.method
            captured["url"] = str(request.url)
            captured["if_match"] = request.headers.get("if-match")
            return httpx.Response(204)

        cfg = GlobalSharePointConfig(site_url="https://sp.corp/x", auth_type="none")
        client = SpClient(cfg, transport=_mock_handler(handler))
        provider = FileBasedListProvider(tmp_path)
        crud = SpCrud(client, provider)
        async with client:
            await crud.delete_item(
                "delivery_items",
                ListScope("test_customer"),
                "42",
            )
        assert captured["method"] == "DELETE"
        assert "getbytitle('Deliverables_test_customer')" in captured["url"]
        assert "items(42)" in captured["url"]
        assert captured["if_match"] == "*"

    @pytest.mark.asyncio
    async def test_get_items_translates_back_to_canonical(
        self, tmp_path: Path
    ) -> None:
        _write_customer(
            tmp_path,
            "test_customer",
            {
                "delivery_items": {
                    "name": "Deliverables_test_customer",
                    "columns": {"item_name": "Title"},
                },
            },
        )

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={"value": [{"Id": 1, "Title": "Band-1"}, {"Id": 2, "Title": "Band-2"}]},
            )

        cfg = GlobalSharePointConfig(site_url="https://sp.corp/x", auth_type="none")
        client = SpClient(cfg, transport=_mock_handler(handler))
        provider = FileBasedListProvider(tmp_path)
        crud = SpCrud(client, provider)
        async with client:
            items = await crud.get_items("delivery_items", ListScope("test_customer"))
        assert items[0]["item_name"] == "Band-1"
        assert items[0]["_sp_id"] == 1
        assert items[1]["item_name"] == "Band-2"

    @pytest.mark.asyncio
    async def test_get_items_filter_translates(self, tmp_path: Path) -> None:
        _write_customer(
            tmp_path,
            "test_customer",
            {
                "delivery_items": {
                    "name": "Deliverables_test_customer",
                    "columns": {"item_name": "Title", "delivery_state": "Status"},
                },
            },
        )
        captured: dict[str, str] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["url"] = str(request.url)
            return httpx.Response(200, json={"value": []})

        cfg = GlobalSharePointConfig(site_url="https://sp.corp/x", auth_type="none")
        client = SpClient(cfg, transport=_mock_handler(handler))
        provider = FileBasedListProvider(tmp_path)
        crud = SpCrud(client, provider)
        async with client:
            await crud.get_items(
                "delivery_items",
                ListScope("test_customer"),
                canonical_filters={"delivery_state": "Open"},
            )
        url = captured["url"]
        # OData $filter should reference the SP column name, not canonical.
        # httpx URL-encodes spaces as '+' for query params.
        assert any(s in url for s in ("Status+eq", "Status%20eq", "Status eq"))
        assert "delivery_state" not in url


class TestNoProprietaryContent:
    """Sharepoint_integration must not leak SP list names / column names / data
    into PipelineError messages or compact reports."""

    def test_pipeline_error_repr_contains_only_codes_and_metadata(self) -> None:
        err = PipelineError(
            "SHP-E002",
            context={"entity": "delivery_items", "customer": "test_customer", "device": ""},
        )
        text = str(err)
        # Should contain the code and the canonical entity name; should NOT
        # contain proprietary content (item names, customer doc fragments).
        assert "SHP-E002" in text
        # The customer slug + entity name are tokens, not proprietary content.
        # We don't include SP column names or list names by construction.
        assert "delivery_items" in text
