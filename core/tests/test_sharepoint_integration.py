"""Unit tests for sharepoint_integration core.

Post architect-lock 2026-06-25 refactor: SpClient is driven via an
injected `SpSession` (`MockSpSession` for in-memory tests). The old
`httpx.MockTransport` injection is gone — SpSession owns the transport
and the digest dance.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from core.src.diagnostics import PipelineError
from core.src.sharepoint_integration import (
    FileBasedListProvider,
    GlobalSharePointConfig,
    ListScope,
    SpClient,
    SpCrud,
    SpSession,
    list_item_type,
)
from core.src.sharepoint_integration.mock_server import MockSpSession, build_app


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
        c2 = GlobalSharePointConfig.from_sources(config_path=cfg_file)
        assert c2.timeout_seconds == 20  # env beats file

    def test_extra_fields_forbidden(self) -> None:
        with pytest.raises(Exception):
            GlobalSharePointConfig(
                site_url="https://sp.corp/x", auth_type="none", unknown="x"
            )  # type: ignore[call-arg]


# --- list_item_type encoding (architect lock 2026-06-25) -------------------


class TestListItemType:
    """Architect Q1-Q4 lock 2026-06-25: `SP.Data.<list>_x005f_<carrier>ListItem`
    with `_` → `_x005f_` and ` ` → `_x0020_` encoding."""

    def test_underscore_encoding(self) -> None:
        assert (
            list_item_type("Deliverables_test_customer")
            == "SP.Data.Deliverables_x005f_test_x005f_customerListItem"
        )

    def test_space_encoding(self) -> None:
        assert (
            list_item_type("My List")
            == "SP.Data.My_x0020_ListListItem"
        )

    def test_mixed_encoding(self) -> None:
        assert (
            list_item_type("Foo Bar_baz")
            == "SP.Data.Foo_x0020_Bar_x005f_bazListItem"
        )

    def test_no_special_chars(self) -> None:
        assert list_item_type("Projects") == "SP.Data.ProjectsListItem"


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


# --- SpSession (digest dance + MERGE protocol) ----------------------------


class _FakeResponse:
    def __init__(
        self,
        status_code: int = 200,
        json_body: Any = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.status_code = status_code
        self._json_body = json_body
        self.headers = headers or {}
        self.content = json.dumps(json_body).encode() if json_body is not None else b""

    def json(self) -> Any:
        if self._json_body is None:
            raise ValueError("no body")
        return self._json_body


class _RecordingSession:
    """Stand-in for requests.Session that records calls + serves canned
    responses keyed by URL + method."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict[str, Any]]] = []
        self.headers: dict[str, str] = {}
        self.auth: Any = None
        self._responses: dict[tuple[str, str], list[_FakeResponse]] = {}

    def queue(self, method: str, url: str, response: _FakeResponse) -> None:
        self._responses.setdefault((method, url), []).append(response)

    def _pop(self, method: str, url: str) -> _FakeResponse:
        queue = self._responses.get((method, url))
        if not queue:
            raise AssertionError(f"unexpected {method} {url}")
        return queue.pop(0)

    def get(
        self, url: str, params: dict[str, Any] | None = None, **kw: Any
    ) -> _FakeResponse:
        self.calls.append(("GET", url, {"params": params, **kw}))
        return self._pop("GET", url)

    def post(
        self,
        url: str,
        headers: dict[str, str] | None = None,
        data: Any = None,
        **kw: Any,
    ) -> _FakeResponse:
        self.calls.append(("POST", url, {"headers": headers, "data": data, **kw}))
        return self._pop("POST", url)

    def close(self) -> None:
        pass


def _make_session_with_fake(rec: _RecordingSession, site_url: str) -> SpSession:
    """Build a real SpSession but swap its requests.Session for `rec`."""
    sess = SpSession.__new__(SpSession)
    sess._site_url = site_url.rstrip("/")
    sess._session = rec  # type: ignore[assignment]
    sess._cookie = None
    sess._digest = None
    return sess


class TestSpSessionDigestDance:
    def test_lazy_digest_acquired_on_first_write(self) -> None:
        rec = _RecordingSession()
        site = "https://sp.corp/sp/tg/site"
        # Step 1: GET site root returns Set-Cookie
        rec.queue("GET", site, _FakeResponse(
            200, json_body={"d": {}}, headers={"Set-Cookie": "WSSAUTH=c1; Path=/"}
        ))
        # Step 2: POST /_api/contextinfo returns FormDigestValue
        rec.queue("POST", f"{site}/_api/contextinfo", _FakeResponse(
            200, json_body={"d": {"GetContextWebInformation": {"FormDigestValue": "DIG1"}}}
        ))
        # The MERGE itself
        merge_url = f"{site}/_api/web/lists/getbytitle('Deliverables_test_customer')/items(7)"
        rec.queue("POST", merge_url, _FakeResponse(204))

        sess = _make_session_with_fake(rec, site)
        status = sess.merge(
            "Deliverables_test_customer", "test_customer", 7,
            {"Title": "v2"},
        )
        assert status == 204
        # Verify the dance happened + headers carry digest
        methods = [(m, u) for m, u, _ in rec.calls]
        assert methods == [
            ("GET", site),
            ("POST", f"{site}/_api/contextinfo"),
            ("POST", merge_url),
        ]
        merge_call = rec.calls[2][2]
        assert merge_call["headers"]["X-Http-Method"] == "MERGE"
        assert merge_call["headers"]["X-RequestDigest"] == "DIG1"
        assert merge_call["headers"]["IF-MATCH"] == "*"
        assert merge_call["headers"]["Cookie"] == "WSSAUTH=c1"
        # body wraps __metadata with correct type
        body = json.loads(merge_call["data"])
        assert body["__metadata"]["type"] == list_item_type("Deliverables_test_customer")
        assert body["Title"] == "v2"

    def test_403_triggers_digest_refresh_and_retry(self) -> None:
        rec = _RecordingSession()
        site = "https://sp.corp/sp/tg/site"
        merge_url = f"{site}/_api/web/lists/getbytitle('Deliverables_test_customer')/items(7)"
        # initial dance
        rec.queue("GET", site, _FakeResponse(200, {"d": {}}, {"Set-Cookie": "WSSAUTH=c1"}))
        rec.queue("POST", f"{site}/_api/contextinfo", _FakeResponse(
            200, {"d": {"GetContextWebInformation": {"FormDigestValue": "DIG1"}}}
        ))
        # first merge: 403
        rec.queue("POST", merge_url, _FakeResponse(403))
        # refresh
        rec.queue("GET", site, _FakeResponse(200, {"d": {}}, {"Set-Cookie": "WSSAUTH=c2"}))
        rec.queue("POST", f"{site}/_api/contextinfo", _FakeResponse(
            200, {"d": {"GetContextWebInformation": {"FormDigestValue": "DIG2"}}}
        ))
        # retry: 204
        rec.queue("POST", merge_url, _FakeResponse(204))

        sess = _make_session_with_fake(rec, site)
        status = sess.merge(
            "Deliverables_test_customer", "test_customer", 7, {"Title": "v2"},
        )
        assert status == 204
        # 2 GETs + 2 contextinfo + 2 merge posts
        get_calls = [c for c in rec.calls if c[0] == "GET"]
        ctx_calls = [c for c in rec.calls if c[1].endswith("/_api/contextinfo")]
        merge_calls = [c for c in rec.calls if c[1] == merge_url]
        assert len(get_calls) == 2
        assert len(ctx_calls) == 2
        assert len(merge_calls) == 2
        # second merge used the refreshed digest
        assert merge_calls[1][2]["headers"]["X-RequestDigest"] == "DIG2"
        assert merge_calls[1][2]["headers"]["Cookie"] == "WSSAUTH=c2"

    def test_get_does_not_require_digest(self) -> None:
        rec = _RecordingSession()
        site = "https://sp.corp/sp/tg/site"
        list_url = f"{site}/_api/web/lists/getbytitle('Projects_test_customer')/items"
        rec.queue("GET", list_url, _FakeResponse(200, {"value": []}))
        sess = _make_session_with_fake(rec, site)
        resp = sess.get(list_url)
        assert resp.status_code == 200
        # No contextinfo call should have happened
        assert all(not u.endswith("/_api/contextinfo") for _, u, _ in rec.calls)


# --- SpClient + SpCrud against the mock-server --------------------------


class TestSpClientAgainstMock:
    @pytest.mark.asyncio
    async def test_create_get_round_trip(self, tmp_path: Path) -> None:
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
                {"item_name": "Band-1", "owner_email": "rd@corp.com"},
            )
            assert item_id == "1"
            items = await crud.get_items(
                "delivery_items", ListScope("test_customer")
            )
            assert items[0]["item_name"] == "Band-1"
            assert items[0]["_sp_id"] == 1

    @pytest.mark.asyncio
    async def test_update_uses_merge_protocol(self, tmp_path: Path) -> None:
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
        app = build_app()
        session = MockSpSession(app)
        cfg = GlobalSharePointConfig(site_url="http://mock-sp", auth_type="none")
        client = SpClient(cfg, session=session)
        provider = FileBasedListProvider(tmp_path)
        crud = SpCrud(client, provider)
        async with client:
            item_id = await crud.create_item(
                "delivery_items",
                ListScope("test_customer"),
                {"item_name": "Band-1", "delivery_state": "Open"},
            )
            await crud.update_item(
                "delivery_items",
                ListScope("test_customer"),
                item_id,
                {"delivery_state": "Closed"},
            )
            items = await crud.get_items(
                "delivery_items", ListScope("test_customer")
            )
            assert items[0]["delivery_state"] == "Closed"

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
        app = build_app()
        session = MockSpSession(app)
        cfg = GlobalSharePointConfig(site_url="http://mock-sp", auth_type="none")
        client = SpClient(cfg, session=session)
        provider = FileBasedListProvider(tmp_path)
        crud = SpCrud(client, provider)
        async with client:
            await crud.create_item(
                "delivery_items",
                ListScope("test_customer"),
                {"item_name": "Band-1", "owner_email": "rd@corp.com"},
            )
        # Inspect the stored record — translation happened
        store = app.state.store
        items = list(store.iter_items("Deliverables_test_customer"))
        assert items == [{"Id": 1, "Title": "Band-1", "Owner_Email": "rd@corp.com"}]

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
        app = build_app()
        # Pre-seed
        app.state.store.create_item("Deliverables_test_customer", {"Title": "Band-1"})
        app.state.store.create_item("Deliverables_test_customer", {"Title": "Band-2"})
        session = MockSpSession(app)
        cfg = GlobalSharePointConfig(site_url="http://mock-sp", auth_type="none")
        client = SpClient(cfg, session=session)
        provider = FileBasedListProvider(tmp_path)
        crud = SpCrud(client, provider)
        async with client:
            items = await crud.get_items(
                "delivery_items", ListScope("test_customer")
            )
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
        app = build_app()
        app.state.store.create_item(
            "Deliverables_test_customer", {"Title": "a", "Status": "Open"}
        )
        app.state.store.create_item(
            "Deliverables_test_customer", {"Title": "b", "Status": "Closed"}
        )
        session = MockSpSession(app)
        cfg = GlobalSharePointConfig(site_url="http://mock-sp", auth_type="none")
        client = SpClient(cfg, session=session)
        provider = FileBasedListProvider(tmp_path)
        crud = SpCrud(client, provider)
        async with client:
            items = await crud.get_items(
                "delivery_items",
                ListScope("test_customer"),
                canonical_filters={"delivery_state": "Open"},
            )
        assert len(items) == 1
        assert items[0]["item_name"] == "a"

    @pytest.mark.asyncio
    async def test_delete_item(self, tmp_path: Path) -> None:
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
        app = build_app()
        session = MockSpSession(app)
        cfg = GlobalSharePointConfig(site_url="http://mock-sp", auth_type="none")
        client = SpClient(cfg, session=session)
        provider = FileBasedListProvider(tmp_path)
        crud = SpCrud(client, provider)
        async with client:
            item_id = await crud.create_item(
                "delivery_items",
                ListScope("test_customer"),
                {"item_name": "x"},
            )
            await crud.delete_item(
                "delivery_items", ListScope("test_customer"), item_id
            )
            items = await crud.get_items(
                "delivery_items", ListScope("test_customer")
            )
            assert items == []


class TestSpClientErrorPaths:
    @pytest.mark.asyncio
    async def test_4xx_on_get_raises_shp_e001(self, tmp_path: Path) -> None:
        _write_customer(
            tmp_path,
            "test_customer",
            {
                "delivery_items": {
                    "name": "Missing_list",
                    "columns": {"item_name": "Title"},
                },
            },
        )
        app = build_app()
        session = MockSpSession(app)
        cfg = GlobalSharePointConfig(
            site_url="http://mock-sp", auth_type="none", max_retries=0
        )
        client = SpClient(cfg, session=session)
        provider = FileBasedListProvider(tmp_path)
        crud = SpCrud(client, provider)
        async with client:
            with pytest.raises(PipelineError) as ei:
                await crud.get_items(
                    "delivery_items", ListScope("test_customer")
                )
        assert ei.value.code_id == "SHP-E001"
        assert ei.value.context["status"] == 404

    @pytest.mark.asyncio
    async def test_ntlm_missing_creds_raises_shp_e004(self) -> None:
        cfg = GlobalSharePointConfig(
            site_url="https://sp.corp/sp/tg/x", auth_type="ntlm"
        )
        with pytest.raises(PipelineError) as ei:
            SpClient(cfg)
        assert ei.value.code_id == "SHP-E004"


class TestNoProprietaryContent:
    """Sharepoint_integration must not leak SP list names / column names / data
    into PipelineError messages or compact reports."""

    def test_pipeline_error_repr_contains_only_codes_and_metadata(self) -> None:
        err = PipelineError(
            "SHP-E002",
            context={"entity": "delivery_items", "customer": "test_customer", "device": ""},
        )
        text = str(err)
        assert "SHP-E002" in text
        assert "delivery_items" in text

    def test_sp_session_repr_omits_password_and_cookie(self) -> None:
        rec = _RecordingSession()
        sess = _make_session_with_fake(rec, "https://sp.corp/sp/tg/x")
        sess._cookie = "WSSAUTH=should-not-appear"  # pragma: no mutate
        sess._digest = "secret-digest"
        text = repr(sess)
        assert "should-not-appear" not in text
        assert "secret-digest" not in text
