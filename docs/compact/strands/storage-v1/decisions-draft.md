# Draft decisions — storage-v1

*Promoted to canonical DECISIONS.md with sequential D-XXX IDs at `/land-strand`.*

---

## DRAFT-1: Storage holds no DeliveryItem mirror — entity resolution is the caller's job (caller-resolves discipline)

**Date:** 2026-06-11
**Status:** draft — architect-directed 2026-06-11 (review of implementation session 1); capture for canonical record at land-strand

**Context:** Two storage APIs in the MODULE.md contract need DeliveryItem attributes the
module doesn't store: `get_default_work_item_for_milestone` (FR-78) needs the milestone's
`item_type = Default` item, and `set_folder_routing_for_tg` (FR-77) validates `item_no`
against the milestone's items; `reassign_document_to_workitem` (FR-83) needs the target
item's `tg_name` / `owner_email` / `plm_id`. The initial implementation closed the gap with
a minimal `DeliveryItemMirrorTable` + `upsert_delivery_item_mirror` populated by `tracker`.

**Decision (architect):** No DeliveryItem schema in storage. (a) Mirror table + upsert
removed. (b) `get_default_work_item_for_milestone` removed from storage entirely — the
FR-52 caller resolves the default work-item via `sharepoint_integration` and fires
`INSTANTIATE_DEFAULT_WORK_ITEM` (STR-W003) when absent. (c) `reassign_document_to_workitem`
takes explicit `target_tg_name` / `target_owner_email` / `target_plm_id` keyword params.
(d) `set_folder_routing_for_tg` takes required `valid_item_nos: set[int]`. In all cases
the workflow_engine task body performs the SP `get_items` lookup BEFORE calling storage.

**Why:** Keeps storage/MODULE.md clean of DeliveryItem schema; avoids a bidirectional
tracker↔storage dependency (tracker already depends on storage); avoids the single-writer
discipline burden a mirror imposes (who upserts, when, what staleness is acceptable);
SP remains the one canonical entity store per `[D-064]` writeback model. Alternative
(minimal mirror) rejected as scope creep that grows silently as more entity attributes
get requested.

**Consequences:** workflow_engine task bodies (FR-83 reassignment, FR-77 routing-table
update, FR-52 step-5 default-work-item landing) each perform one SP read before the
storage call — acceptable latency on TPM-triggered paths. storage/MODULE.md Public
surface updated 2026-06-11 (rollback-log entry); affected signatures are keyword-explicit
so future entity attributes arrive as new params, not hidden lookups. STR-W003 remains
registered as the caller-side signal.

---

## DRAFT-2: `doc_id_slug` / `rev_number` nullable with staged-fill lifecycle; partial unique index replaces full UNIQUE

**Date:** 2026-06-11
**Status:** draft — architect-directed (full patch spec provided 2026-06-11); applied via architecture-phase pass

**Context:** `DocumentIndexRow` carried an internal contradiction: its class docstring
(FR-86 storage matrix) required `doc_id_slug` + `rev_number` to be NULL while a file sits
on a staged/unrouted NSD path awaiting FR-87 TPM resolution, but the field declarations
were non-null (carried over from the pre-`[D-053]` 2026-05-24 initial draft, before
staged-fill timing existed). The full `UNIQUE (milestone_id, doc_id_slug, rev_number)`
constraint was likewise wrong: SQL NULL doesn't deduplicate, so it would either reject
legitimate co-existing staged-NULL rows or behave backend-dependently.

**Decision (architect):** Both fields are nullable. Staged-fill lifecycle invariant:
NULL between ingest and resolution (classification + `[D-039]` revision determination);
both populated together atomically when the file moves to the `classified` path; never
reverted to NULL. Secondary uniqueness becomes a **partial unique index**
`(milestone_id, doc_id_slug, rev_number) WHERE doc_id_slug IS NOT NULL AND rev_number
IS NOT NULL`. NULL-handling contract: `get_document_index_row_by_slug` cannot find
staged rows by design (use hash lookup or `nsd_path_type` queries);
`list_revisions` filters NULL-slug rows out.

**Why:** The docstring (FR-86) was correct; the field declaration was the bug. Nullable-
with-lifecycle beats sentinel values ("_unresolved" slugs would pollute the slug
namespace and the partial-index contract). Partial unique index preserves the FR-57
exactly-one-file lookup guarantee for resolved rows while allowing any number of
pre-resolution rows.

**Consequences:** Storage-only — no requirements.md change (FR-86 already specified the
timing), no template_schema change. Callers must treat NULL as "pre-resolution", not
error. Migration note: the architect's patch specified a separate Alembic revision
(alter NOT NULL + swap constraint for partial index); since the 0001 baseline has never
shipped (no deployed DB, not yet pushed), the change was folded into the
metadata-driven baseline instead — flagged for architect acknowledgment.
