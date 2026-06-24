"""email_service test suite -- protocol + config + classifier + parsers +
Fr52AttachmentRouter + tg_resolver + sp_alert_parser + outbound composers +
SmtpSender + error codes + CLI smoke.

Per email_service/MODULE.md test-scenario minimum subset (single-TG single-doc
single-owner Ph-1 happy path) + per-test-coverage rules in the spec.

Uses MockImapReceiver / MockSmtpSender / FakeCredentialService / InMemoryStorage
from core.src.email_service.mocks; uses MockLLM from core.src.llm.mock.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from core.src.diagnostics.error_codes import ERROR_CODES, PipelineError
from core.src.email_service import (
    AttachmentItemMatch,
    ClassificationResolution,
    EmailKind,
    EmailServiceConfig,
    Fr52AttachmentRouter,
    Fr52Config,
    ImapConfig,
    InboundAttachment,
    InboundMessage,
    SmtpConfig,
    SmtpSender,
    SpAlertParser,
    classify,
    compose_outreach,
    compose_reminder,
    extract_routing_key,
    get_template_env,
    parse_freetext_with_attachments,
    parse_structured_block,
    parse_subject,
    resolve_tg_from_email,
)
from core.src.email_service.email_service_cli import _cmd_diagnostic, main as cli_main
from core.src.email_service.inbound.body_parser_structured import resolve_sender_match
from core.src.email_service.mocks import (
    FakeCredentialService,
    InMemoryStorage,
    MockImapReceiver,
    MockSmtpSender,
)
from core.src.llm.mock import MockLLM
from core.src.llm.protocol import TaskKind
from core.src.storage.models import NSDPathType, RoutingResolution
from core.src.template_schema.enums import DocType, ItemType


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _msg(
    *,
    subject: str = "",
    body: str = "",
    sender: str = "owner@corp.example",
    to_addrs: tuple[str, ...] = (),
    cc_addrs: tuple[str, ...] = (),
    attachments: tuple[InboundAttachment, ...] = (),
    message_id: str = "<test@local>",
) -> InboundMessage:
    return InboundMessage(
        message_id=message_id,
        received_at=datetime(2026, 6, 25, 10, 0, 0, tzinfo=timezone.utc),
        sender=sender,
        to_addrs=to_addrs,
        cc_addrs=cc_addrs,
        subject=subject,
        body_text=body,
        body_html=None,
        attachments=attachments,
    )


def _attachment(filename: str, content: bytes = b"hello", file_hash: str | None = None) -> InboundAttachment:
    import hashlib
    if file_hash is None:
        file_hash = hashlib.sha256(content).hexdigest()
    return InboundAttachment(
        filename=filename,
        content=content,
        content_type="application/pdf",
        file_hash=file_hash,
    )


def _candidate_test_item(
    *,
    item_id: str = "ITEM-1",
    item_no: int = 1,
    item_name: str = "test_report_5g_band_n78",
    item_type: str = ItemType.TEST_TECH_WAIVER_REPORT.value,
    item_description: Any = "5G,n78",
    tg_name: str | None = "TG-Alpha",
    owner_corp_usa_email: str = "owner@corp.example",
    owner_corp_id: str = "owner-001",
    tg_email_group_alias: str | None = None,
    folder_routing_enabled: bool = False,
) -> dict:
    return {
        "item_id":               item_id,
        "item_no":               item_no,
        "item_name":             item_name,
        "item_description":      item_description,
        "item_type":             item_type,
        "tg_name":               tg_name,
        "tg_email_group_alias":  tg_email_group_alias,
        "owner_corp_usa_email":  owner_corp_usa_email,
        "owner_corp_email":      owner_corp_usa_email,
        "owner_corp_id":         owner_corp_id,
        "folder_routing_enabled": folder_routing_enabled,
    }


def _default_item(item_id: str = "ITEM-DEFAULT") -> dict:
    return {
        "item_id":               item_id,
        "item_no":               99,
        "item_name":             "default work-item",
        "item_description":      None,
        "item_type":             ItemType.DEFAULT.value,
        "tg_name":               None,
        "tg_email_group_alias":  None,
        "owner_corp_usa_email":  None,
        "owner_corp_email":      None,
        "owner_corp_id":         "default-owner",
        "folder_routing_enabled": False,
    }


def _make_router(
    *,
    storage: InMemoryStorage | None = None,
    llm: MockLLM | None = None,
    plm_upload_enabled: bool = False,
    review_required_enabled: bool = False,
    max_matches_threshold: int = 10,
) -> Fr52AttachmentRouter:
    return Fr52AttachmentRouter(
        storage=storage or InMemoryStorage(),
        llm=llm,
        tg_resolver=None,
        doc_type_filename_rules_path=Path(
            "core/src/email_service/default_doc_type_rules.yaml"
        ),
        plm_upload_enabled=plm_upload_enabled,
        review_required_enabled=review_required_enabled,
        route_attachment_max_matches_threshold=max_matches_threshold,
    )


# ===========================================================================
# TestEmailKind / TestClassifier
# ===========================================================================


class TestEmailKind:
    def test_enum_values(self):
        assert EmailKind.OWNER_REPLY.value == "owner_reply"
        assert EmailKind.SP_ALERT.value == "sp_alert"
        assert EmailKind.OTHER.value == "other"


class TestClassifier:
    def test_classifies_owner_reply_via_batch_id(self):
        msg = _msg(subject="Re: [HILDA] Status request -- BATCH-abc123")
        assert classify(msg) == EmailKind.OWNER_REPLY

    def test_classifies_sp_alert(self):
        msg = _msg(subject="Alert_Deliverables_acme - Item changed")
        assert classify(msg) == EmailKind.SP_ALERT

    def test_classifies_other(self):
        msg = _msg(subject="Out of office reply")
        assert classify(msg) == EmailKind.OTHER

    def test_sp_alert_wins_over_batch_id_in_subject(self):
        # If a subject somehow contains both -- SP alert pattern (anchored) wins
        msg = _msg(subject="Alert_Projects_acme - BATCH-xyz changed")
        assert classify(msg) == EmailKind.SP_ALERT


# ===========================================================================
# TestSubjectParser
# ===========================================================================


class TestSubjectParser:
    def test_extracts_batch_id(self):
        parsed = parse_subject("Re: [HILDA] -- BATCH-abc123")
        assert parsed.batch_id == "BATCH-abc123"
        assert parsed.item_no is None
        assert parsed.status is None

    def test_raises_on_missing_batch_id(self):
        with pytest.raises(PipelineError) as exc_info:
            parse_subject("No batch token here")
        assert exc_info.value.code_id == "EML-E001"

    def test_extracts_item_no_and_status(self):
        # Ph-2 mailto tap-link subject shape
        parsed = parse_subject("[HILDA] BATCH-xyz ITEM-3 OWNER_CLOSED")
        assert parsed.batch_id == "BATCH-xyz"
        assert parsed.item_no == 3
        assert parsed.status == "OWNER_CLOSED"


# ===========================================================================
# TestBodyParserStructured
# ===========================================================================


class TestBodyParserStructured:
    def test_parses_single_item_block(self):
        body = (
            "Hi,\n\nHILDA STATUS BLOCK\nBATCH-abc\n"
            "ITEM-1 OWNER_CLOSED: All test cases passed.\n"
        )
        msg = _msg(subject="Re: BATCH-abc", body=body, sender="owner@corp.example")
        expected = [_candidate_test_item(owner_corp_usa_email="owner@corp.example")]
        block = parse_structured_block(msg, "BATCH-abc", expected)
        assert block is not None
        assert block.batch_id == "BATCH-abc"
        assert block.sender_match == "owner"
        assert len(block.per_item_updates) == 1
        update = block.per_item_updates[0]
        assert update.item_no == 1
        assert update.delivery_state == "OWNER_CLOSED"
        assert update.owner_status_note == "All test cases passed."
        assert update.confidence == 1.0

    def test_returns_none_when_block_missing(self):
        msg = _msg(subject="Re: BATCH-abc", body="Hi, here is my reply.")
        assert parse_structured_block(msg, "BATCH-abc", []) is None

    def test_returns_none_when_batch_id_mismatch(self):
        body = "HILDA STATUS BLOCK\nBATCH-different\nITEM-1 OPEN: x\n"
        msg = _msg(subject="Re: BATCH-abc", body=body)
        assert parse_structured_block(msg, "BATCH-abc", []) is None

    def test_parses_multiple_items(self):
        body = (
            "HILDA STATUS BLOCK\nBATCH-x\n"
            "ITEM-1 OWNER_CLOSED: ok\n"
            "ITEM-2 DELAYED: waiting on test rig\n"
        )
        msg = _msg(subject="Re: BATCH-x", body=body)
        block = parse_structured_block(msg, "BATCH-x", [])
        assert block is not None
        assert len(block.per_item_updates) == 2
        assert block.per_item_updates[0].item_no == 1
        assert block.per_item_updates[1].delivery_state == "DELAYED"

    def test_sender_match_resolves_to_tg_alias(self):
        item = _candidate_test_item(
            owner_corp_usa_email="someone@corp.example",
            tg_email_group_alias="alias@corp.example",
        )
        match = resolve_sender_match(
            "alias@corp.example", (), [item]
        )
        assert match == "tg_alias"

    def test_sender_match_resolves_to_cc(self):
        item = _candidate_test_item(owner_corp_usa_email="someone@corp.example")
        match = resolve_sender_match(
            "third@corp.example", ("third@corp.example",), [item]
        )
        assert match == "cc"

    def test_sender_match_mismatch(self):
        item = _candidate_test_item(owner_corp_usa_email="owner@corp.example")
        match = resolve_sender_match("stranger@corp.example", (), [item])
        assert match == "mismatch"


# ===========================================================================
# TestBodyParserFreetext (Ph-2 stub)
# ===========================================================================


class TestBodyParserFreetext:
    async def test_raises_not_implemented_ph2(self):
        msg = _msg(subject="BATCH-x", body="free text")
        with pytest.raises(NotImplementedError) as exc_info:
            await parse_freetext_with_attachments(
                msg, "BATCH-x", [], llm=MockLLM()
            )
        assert "Ph-2" in str(exc_info.value)


# ===========================================================================
# TestFr52AttachmentRouter
# ===========================================================================


class TestFr52AttachmentRouter:
    async def test_dedup_short_circuits(self):
        storage = InMemoryStorage()
        # Pre-load index with the same file_hash
        existing = type("Row", (), {
            "file_hash": "h1", "doc_type": DocType.TEST_REPORT.value,
            "doc_id_slug": "slug1", "rev_number": 1, "inferred_tg_name": "TG-A",
        })()
        storage.index["h1"] = existing
        router = _make_router(storage=storage)
        att = _attachment("test_report.pdf", file_hash="h1")
        result = await router.route(att, "BATCH-x", [_candidate_test_item()])
        assert result.is_duplicate is True
        assert result.file_hash == "h1"

    async def test_happy_path_substring_match_classified(self):
        """Basic flow: test_report.pdf -> test_tech_waiver_report item ->
        SubstringMatch + FilenameRegex + CLASSIFIED."""
        router = _make_router()
        att = _attachment("device_5g_n78_test_report.pdf")
        item = _candidate_test_item(item_description=["5g", "n78"])
        result = await router.route(att, "BATCH-1", [item])
        assert result.classification_resolution == ClassificationResolution.FILENAME_REGEX
        assert result.doc_type == DocType.TEST_REPORT.value
        assert result.routing_resolution == RoutingResolution.SUBSTRING_MATCH
        assert len(result.matches) == 1
        assert result.matches[0].item_id == "ITEM-1"
        assert result.nsd_path_type == NSDPathType.CLASSIFIED
        assert result.doc_id_slug is not None
        assert result.rev_number == 1

    async def test_fuzzy_match_when_substring_fails(self):
        router = _make_router()
        # Filename doesn't contain item_description tags, but item_name is close
        att = _attachment("test_report_v2.pdf")
        item = _candidate_test_item(
            item_name="test_report_v2_for_handset",
            item_description=None,
        )
        result = await router.route(att, "BATCH-1", [item])
        assert result.routing_resolution == RoutingResolution.FUZZY_MATCH
        assert len(result.matches) == 1

    async def test_staged_default_when_no_match(self):
        router = _make_router()
        att = _attachment("random_attachment.pdf")
        # Only Default item available; no tags
        item = _candidate_test_item(item_description=None, item_name="zzzz_nothing")
        default = _default_item()
        result = await router.route(att, "BATCH-1", [item, default])
        assert result.routing_resolution == RoutingResolution.STAGED_DEFAULT
        assert result.matches[0].item_id == "ITEM-DEFAULT"
        assert result.nsd_path_type == NSDPathType.UNROUTED

    async def test_llm_route_attachment_path(self):
        """Step B4: LLM returns matches above threshold -> LLMRouteAttachment."""
        mock_llm = MockLLM()
        mock_llm.register(
            TaskKind.ROUTE_ATTACHMENT,
            {},  # match any inputs
            {"matches": [{"item_id": "ITEM-1", "confidence": 0.92}]},
        )
        router = _make_router(llm=mock_llm)
        # Force a fall-through past substring + fuzzy: blank filename + non-matching item
        att = _attachment("ambiguous.pdf")
        item = _candidate_test_item(
            item_name="totally_different_zzz",
            item_description=None,
        )
        result = await router.route(att, "BATCH-1", [item])
        assert result.routing_resolution == RoutingResolution.LLM_ROUTE_ATTACHMENT
        assert result.matches[0].confidence == pytest.approx(0.92)

    async def test_over_routing_emits_w007_signal(self):
        """FR-79: when N > max_matches_threshold, all commit but flagged."""
        mock_llm = MockLLM()
        # 5 matches above threshold; threshold=3 -> over-route signal
        many_matches = [
            {"item_id": f"ITEM-{i}", "confidence": 0.9} for i in range(1, 6)
        ]
        mock_llm.register(TaskKind.ROUTE_ATTACHMENT, {}, {"matches": many_matches})
        router = _make_router(llm=mock_llm, max_matches_threshold=3)
        att = _attachment("ambiguous.pdf")
        items = [
            _candidate_test_item(item_id=f"ITEM-{i}", item_name="zzz", item_description=None)
            for i in range(1, 6)
        ]
        result = await router.route(att, "BATCH-1", items)
        assert len(result.matches) == 5
        # All committed per FR-79; threshold is over -- caller logs EML-W007

    async def test_unresolved_doc_type_on_unknown_filename(self):
        router = _make_router()
        att = _attachment("xxx_unknownformat.pdf")
        item = _candidate_test_item(item_description=None, item_name="zzz")
        default = _default_item()
        result = await router.route(att, "BATCH-1", [item, default])
        # Filename regex misses -> Ph-1 first cut returns UNRESOLVED
        assert result.doc_type == DocType.UNRESOLVED.value
        assert result.classification_resolution == ClassificationResolution.UNRESOLVED_LOW_CONFIDENCE

    async def test_misaligned_pair_lands_staged_not_classified(self):
        """FR-86: test_tech_waiver_report item + doc_type=compliance_certification_release_notes
        is misaligned -> STAGED_NOT_CLASSIFIED."""
        router = _make_router()
        # Filename matches compliance regex
        att = _attachment("compliance_doc.pdf")
        # item_type is test_tech_waiver_report -> misaligned with compliance doc_type
        item = _candidate_test_item(
            item_description=None,
            item_name="compliance_doc",  # fuzzy will match
        )
        result = await router.route(att, "BATCH-1", [item])
        assert result.doc_type == DocType.COMPLIANCE_CERTIFICATION_RELEASE_NOTES.value
        # The pair (test_tech_waiver_report, compliance_certification_release_notes) is misaligned
        assert result.nsd_path_type == NSDPathType.STAGED_NOT_CLASSIFIED

    async def test_multi_item_match_via_llm(self):
        """FR-79 multi-item association test (NOT over-routing -- just 2 matches)."""
        mock_llm = MockLLM()
        mock_llm.register(
            TaskKind.ROUTE_ATTACHMENT, {},
            {"matches": [
                {"item_id": "ITEM-1", "confidence": 0.9},
                {"item_id": "ITEM-2", "confidence": 0.85},
            ]},
        )
        router = _make_router(llm=mock_llm, max_matches_threshold=10)
        att = _attachment("regression_sweep.pdf")
        items = [
            _candidate_test_item(item_id="ITEM-1", item_name="aaa", item_description=None),
            _candidate_test_item(item_id="ITEM-2", item_name="bbb", item_description=None),
        ]
        result = await router.route(att, "BATCH-1", items)
        assert len(result.matches) == 2
        assert {m.item_id for m in result.matches} == {"ITEM-1", "ITEM-2"}


# ===========================================================================
# TestTgResolver
# ===========================================================================


class TestTgResolver:
    def test_email_group_alias_preferred(self):
        msg = _msg(
            sender="owner@corp.example",
            to_addrs=("alias@corp.example",),
        )
        rows = [
            _candidate_test_item(
                tg_name="TG-Alpha",
                tg_email_group_alias="alias@corp.example",
            ),
        ]
        assert resolve_tg_from_email(msg, rows) == "TG-Alpha"

    def test_falls_back_to_owner_corp_usa_email(self):
        msg = _msg(sender="owner@corp.example")
        rows = [
            _candidate_test_item(
                tg_name="TG-Beta",
                owner_corp_usa_email="owner@corp.example",
                tg_email_group_alias=None,
            ),
        ]
        assert resolve_tg_from_email(msg, rows) == "TG-Beta"

    def test_returns_none_when_no_match(self):
        msg = _msg(sender="stranger@corp.example")
        rows = [_candidate_test_item(tg_name="TG-Gamma")]
        assert resolve_tg_from_email(msg, rows) is None


# ===========================================================================
# TestSpAlertParser
# ===========================================================================


class TestSpAlertParser:
    def test_parses_in_scope_alert(self):
        msg = _msg(
            subject="Alert_Deliverables_acme - Test item changed",
            body="ProjectID: PROJ-1\nMinorMilestone: P1\nItemNumber: 3\n",
        )
        parser = SpAlertParser(storage=_make_fake_sp_storage())
        parsed = parser.parse(msg)
        assert parsed is not None
        assert parsed.routing_key.list_name == "Deliverables"
        assert parsed.routing_key.list_suffix == "acme"
        assert parsed.routing_key.project_id == "PROJ-1"
        assert parsed.routing_key.milestone_name == "P1"
        assert parsed.routing_key.item_number == 3

    def test_drops_out_of_scope_alert(self):
        msg = _msg(
            subject="Alert_TasksTemplate_acme - Item changed",
            body="ProjectID: PROJ-1\n",
        )
        parser = SpAlertParser(storage=_make_fake_sp_storage())
        assert parser.parse(msg) is None

    def test_raises_eml_e007_on_missing_routing_key(self):
        msg = _msg(
            subject="Alert_Projects_acme - Item changed",
            body="totally unrelated body",
        )
        parser = SpAlertParser(storage=_make_fake_sp_storage())
        with pytest.raises(PipelineError) as exc_info:
            parser.parse(msg)
        assert exc_info.value.code_id == "EML-E007"

    def test_routing_key_extractor_handles_aliases(self):
        body = "project_id: P1\nMinorMilestone: P2\nitem_no: 7\n"
        assert extract_routing_key(body) == ("P1", "P2", 7)

    # FR-87 handler tests REMOVED 2026-06-26 per [D-122] cascade
    # (FR-87 step A/B handlers removed from sp_alert_parser; flow now via
    # dashboard direct POST endpoints -- see test_dashboard.py
    # TestFr87ResolveReassign + TestFr87ResolveDocType).


def _make_fake_sp_storage():
    """Tiny SpStorageLike stub recording handler invocations."""
    class _FakeSpStorage:
        def __init__(self) -> None:
            self.reassign_calls: list[dict] = []
            self.resolve_doc_type_calls: list[dict] = []
            self.resolve_revision_calls: list[dict] = []
            self.communications: list = []

        async def reassign_document_to_workitem(self, **kwargs: Any) -> None:
            self.reassign_calls.append(kwargs)

        async def tpm_resolve_doc_type(self, **kwargs: Any) -> None:
            self.resolve_doc_type_calls.append(kwargs)

        async def tpm_resolve_revision(self, **kwargs: Any) -> None:
            self.resolve_revision_calls.append(kwargs)

        async def log_communication(self, row: Any) -> None:
            self.communications.append(row)

    return _FakeSpStorage()


# ===========================================================================
# TestOutboundComposers
# ===========================================================================


class TestOutboundComposers:
    def test_compose_outreach_individual(self):
        env = get_template_env()
        item = _candidate_test_item(tg_email_group_alias=None)
        owner = {
            "owner_corp_usa_email": "owner@corp.example",
            "owner_corp_email":     "owner@corp.example",
            "owner_corp_id":        "owner-001",
            "owner_name":           "Test Owner",
        }
        subject, body = compose_outreach(
            item, owner, "BATCH-xyz", [item], env
        )
        assert "BATCH-xyz" in subject
        assert "HILDA STATUS BLOCK" in body
        assert "BATCH-xyz" in body
        assert "ITEM-1" in body

    def test_compose_outreach_tg_alias(self):
        env = get_template_env()
        items = [
            _candidate_test_item(item_id="ITEM-1", item_no=1,
                                 tg_email_group_alias="alias@corp.example"),
            _candidate_test_item(item_id="ITEM-2", item_no=2,
                                 tg_email_group_alias="alias@corp.example"),
        ]
        owner = {"owner_corp_usa_email": "owner@corp.example",
                 "owner_corp_email": None, "owner_corp_id": "x", "owner_name": "X"}
        subject, body = compose_outreach(
            items[0], owner, "BATCH-tg", items, env
        )
        # tg_alias template should be used when alias set + multiple items
        assert "alias@corp.example" in body
        assert "ITEM-1" in body and "ITEM-2" in body

    def test_compose_reminder(self):
        env = get_template_env()
        item = _candidate_test_item()
        subject, body = compose_reminder(
            item, "BATCH-xyz", "<orig@local>", 2, env
        )
        assert "Reminder (2)" in subject
        assert "BATCH-xyz" in body
        assert "reminder #2" in body or "Reminder" in body or "#2" in body


# ===========================================================================
# TestSmtpSender
# ===========================================================================


class TestSmtpSender:
    async def test_send_success_returns_message_id(self):
        cfg = SmtpConfig()
        cred = FakeCredentialService()
        sender = SmtpSender(cfg, cred)
        msg_id = await sender.send(
            to=["dest@corp.example"], cc=[],
            subject="Test", body="hello",
        )
        assert msg_id.startswith("<") and msg_id.endswith(">")
        # Credential lookup happened exactly once
        assert len(cred.calls) == 1
        assert cred.calls[0][1] == "email"

    async def test_send_raises_eml_e004_on_cred_failure(self):
        cfg = SmtpConfig()
        cred = FakeCredentialService(raise_on_lookup=True)
        sender = SmtpSender(cfg, cred)
        with pytest.raises(PipelineError) as exc_info:
            await sender.send(
                to=["dest@corp.example"], cc=[],
                subject="Test", body="hello",
            )
        assert exc_info.value.code_id == "EML-E004"


# ===========================================================================
# TestErrorCodes
# ===========================================================================


class TestErrorCodes:
    def test_all_eml_codes_registered(self):
        expected = {
            "EML-E001", "EML-E002", "EML-E003", "EML-E004", "EML-E005",
            "EML-E006", "EML-E007",
            "EML-W001", "EML-W002", "EML-W003", "EML-W004",
            "EML-W005", "EML-W006", "EML-W007",
        }
        for code in expected:
            assert code in ERROR_CODES, f"Missing EML code: {code}"


# ===========================================================================
# TestConfig
# ===========================================================================


class TestConfig:
    def test_defaults(self):
        cfg = EmailServiceConfig()
        assert cfg.imap.port == 993
        assert cfg.imap.use_idle is True
        assert cfg.imap.poll_interval_s == 60
        assert cfg.smtp.port == 587
        assert cfg.fr52.fuzzy_threshold == pytest.approx(0.85)
        assert cfg.fr52.route_attachment_max_matches_threshold == 10
        assert cfg.fr52.plm_upload_enabled is True

    def test_overrides(self):
        cfg = EmailServiceConfig(
            imap=ImapConfig(host="exchange.corp", use_idle=False),
            fr52=Fr52Config(fuzzy_threshold=0.9, plm_upload_enabled=False),
        )
        assert cfg.imap.host == "exchange.corp"
        assert cfg.imap.use_idle is False
        assert cfg.fr52.fuzzy_threshold == pytest.approx(0.9)
        assert cfg.fr52.plm_upload_enabled is False


# ===========================================================================
# TestEmailServiceCli
# ===========================================================================


class TestEmailServiceCli:
    def test_diagnostic_smoke(self, capsys):
        cfg = EmailServiceConfig()
        rc = _cmd_diagnostic("run-test", cfg)
        captured = capsys.readouterr()
        assert rc == 0
        # Compact-report line per [D-002] -- starts with RPT|EML|...
        assert "RPT|EML|run-test|" in captured.out
        # NFR-2: no subject/body content -- only enum tokens + counts
        assert "templates_loaded=" in captured.out


# ===========================================================================
# TestComposeEscalation -- REMOVED 2026-06-26 per architect Q-M6 lock 2026-06-25
# (messenger module owns escalation composition; composer_escalation.py deleted).
# ===========================================================================


# ===========================================================================
# TestMockReceiver smoke
# ===========================================================================


class TestMocks:
    async def test_mock_imap_receiver_fetch_once(self):
        msg = _msg(subject="BATCH-x", body="hi")
        receiver = MockImapReceiver(messages=[msg])
        msgs = await receiver.fetch_once()
        assert len(msgs) == 1
        await receiver.mark_processed(msg.message_id)
        assert receiver.processed == [msg.message_id]

    async def test_mock_smtp_sender_records_send(self):
        sender = MockSmtpSender()
        msg_id = await sender.send(to=["a@b"], cc=[], subject="s", body="b")
        assert msg_id.endswith("@mock.local>")
        assert len(sender.sent) == 1
        assert sender.sent[0]["subject"] == "s"
