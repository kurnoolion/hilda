"""SpCrud: the only public CRUD surface for SharePoint integration.

Composes SpClient (HTTP) + SharePointListProvider (HILDA-entity routing).
All other modules call SpCrud, never SpClient or SharePointListProvider directly.
Anchors [D-020].
"""
from __future__ import annotations

from typing import Any

from core.src.sharepoint_integration.config import ListScope
from core.src.sharepoint_integration.list_provider import SharePointListProvider
from core.src.sharepoint_integration.sp_client import SpClient


class SpCrud:
    """Canonical-field-in / canonical-field-out CRUD against SharePoint."""

    def __init__(self, client: SpClient, provider: SharePointListProvider) -> None:
        self._client = client
        self._provider = provider

    async def get_item(
        self,
        entity: str,
        scope: ListScope,
        item_id: int | str,
        expand: list[str] | None = None,
        extra_select: list[str] | None = None,
    ) -> dict[str, Any] | None:
        """Single-row fetch by SP Id per architect lock 2026-06-26.

        Per `[D-074]` Variant A + dashboard cascade 2026-06-26 Gap 7: dashboard
        GET /docs/{customer_id}/{sp_id} calls this to fetch the freshly-current
        SP row on every page load (no caching Ph-1). The `<sp_id>` IS SP's `Id`
        (auto-counter PK) per architect lock 2026-06-26. Returns None when the
        SP row does not exist (404-equivalent at dashboard handler).

        `expand` + `extra_select` per architect Q9 lock 2026-06-26 -- for
        User/Person fields like `Projects.TPM`, caller passes
        expand=["TPM"] + extra_select=["TPM/EMail", "TPM/Title"] to get the
        nested object: `row["TPM"] = {"EMail": "abc@corp.com", "Title": ...}`.
        HILDA derives tpm_corp_id via `row["TPM"]["EMail"].split("@")[0]` per
        [D-088] 3-tuple.
        """
        list_name = self._provider.get_list_name(entity, scope)
        col_map = self._provider.get_column_map(entity, scope)
        select_list = list(col_map.values())
        if extra_select:
            select_list.extend(extra_select)
        select = select_list or None
        items_sp = await self._client.get_list_items(
            list_name,
            select=select,
            filter_expr=f"Id eq {int(item_id)}",
            expand=expand,
        )
        if not items_sp:
            return None
        item = items_sp[0]
        result = self._provider.from_sp_fields(entity, scope, item)
        if "Id" in item:
            result["_sp_id"] = item["Id"]
        elif "ID" in item:
            result["_sp_id"] = item["ID"]
        return result

    async def get_items(
        self,
        entity: str,
        scope: ListScope,
        canonical_filters: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        list_name = self._provider.get_list_name(entity, scope)
        col_map = self._provider.get_column_map(entity, scope)
        select = list(col_map.values()) or None
        filter_expr: str | None = None
        if canonical_filters:
            sp_fields = self._provider.to_sp_fields(entity, scope, canonical_filters)
            filter_expr = " and ".join(
                f"{k} eq {_odata_literal(v)}" for k, v in sp_fields.items()
            )
        items_sp = await self._client.get_list_items(
            list_name, select=select, filter_expr=filter_expr
        )
        return [
            {
                **self._provider.from_sp_fields(entity, scope, item),
                **({"_sp_id": item["Id"]} if "Id" in item else {}),
                **({"_sp_id": item["ID"]} if "ID" in item and "Id" not in item else {}),
            }
            for item in items_sp
        ]

    async def create_item(
        self,
        entity: str,
        scope: ListScope,
        canonical_fields: dict[str, Any],
    ) -> str:
        list_name = self._provider.get_list_name(entity, scope)
        sp_fields = self._provider.to_sp_fields(entity, scope, canonical_fields)
        return await self._client.create_list_item(
            list_name, sp_fields, customer_id=scope.customer_id
        )

    async def update_item(
        self,
        entity: str,
        scope: ListScope,
        item_id: str,
        canonical_fields: dict[str, Any],
    ) -> None:
        list_name = self._provider.get_list_name(entity, scope)
        sp_fields = self._provider.to_sp_fields(entity, scope, canonical_fields)
        await self._client.update_list_item(
            list_name, item_id, sp_fields, customer_id=scope.customer_id
        )

    async def delete_item(
        self, entity: str, scope: ListScope, item_id: str
    ) -> None:
        list_name = self._provider.get_list_name(entity, scope)
        await self._client.delete_list_item(list_name, item_id)

    async def batch_create(
        self,
        entity: str,
        scope: ListScope,
        items: list[dict[str, Any]],
    ) -> list[str]:
        list_name = self._provider.get_list_name(entity, scope)
        sp_items = [
            self._provider.to_sp_fields(entity, scope, c) for c in items
        ]
        return await self._client.batch_create(
            list_name, sp_items, customer_id=scope.customer_id
        )

    async def batch_update(
        self,
        entity: str,
        scope: ListScope,
        updates: list[tuple[str, dict[str, Any]]],
    ) -> None:
        list_name = self._provider.get_list_name(entity, scope)
        sp_updates = [
            (item_id, self._provider.to_sp_fields(entity, scope, c))
            for item_id, c in updates
        ]
        await self._client.batch_update(
            list_name, sp_updates, customer_id=scope.customer_id
        )


def _odata_literal(v: Any) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return str(v)
    if v is None:
        return "null"
    return "'" + str(v).replace("'", "''") + "'"
