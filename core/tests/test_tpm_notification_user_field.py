"""TPM-1 (2026-07-27) — SP User/PersonOrGroup field extraction in
tpm_notification._extract_user_field_email_name.

Projects_<customer_id>'s TPM column is a User/PersonOrGroup SP field,
not a plain string. SP REST returns it as a nested dict (or list of
dicts for multi-user fields) when $expand is applied. The extractor
must pull EMail + Title out of any of the common shapes.
"""
from __future__ import annotations

from core.src.workflow_engine.tasks.tpm_notification import (
    _extract_user_field_email_name,
)


class TestNoneAndEmpty:
    def test_none_returns_double_none(self):
        assert _extract_user_field_email_name(None) == (None, None)

    def test_empty_string_returns_double_none(self):
        assert _extract_user_field_email_name("") == (None, None)

    def test_whitespace_string_returns_double_none(self):
        assert _extract_user_field_email_name("   ") == (None, None)

    def test_empty_dict_returns_double_none(self):
        assert _extract_user_field_email_name({}) == (None, None)

    def test_empty_list_returns_double_none(self):
        assert _extract_user_field_email_name([]) == (None, None)


class TestStringShape:
    def test_bare_email_string_returns_email_none(self):
        assert _extract_user_field_email_name("t.arasu@samsung.com") == (
            "t.arasu@samsung.com", None,
        )

    def test_stripped(self):
        assert _extract_user_field_email_name("  t.arasu@samsung.com  ") == (
            "t.arasu@samsung.com", None,
        )


class TestSpUserFieldDict:
    """Canonical SP User field shape after $expand=Field&$select=Field/EMail,Field/Title."""

    def test_sp_pascalcase_email_plus_title(self):
        field = {
            "Id":        12,
            "EMail":     "t.arasu@samsung.com",
            "Title":     "Tarasu Arasu",
            "LoginName": "i:0#.w|corp\\tarasu",
        }
        assert _extract_user_field_email_name(field) == (
            "t.arasu@samsung.com", "Tarasu Arasu",
        )

    def test_lowercase_email_fallback(self):
        field = {"email": "x@y.com", "Title": "X Y"}
        assert _extract_user_field_email_name(field) == ("x@y.com", "X Y")

    def test_mail_fallback(self):
        field = {"mail": "z@w.com", "DisplayName": "Z W"}
        assert _extract_user_field_email_name(field) == ("z@w.com", "Z W")

    def test_email_present_name_absent(self):
        field = {"EMail": "solo@x.com"}
        assert _extract_user_field_email_name(field) == ("solo@x.com", None)

    def test_name_present_email_absent(self):
        # Won't produce a usable result for the caller (needs email); helper
        # still returns the extracted name for potential fallback use.
        field = {"Title": "Named-No-Email"}
        assert _extract_user_field_email_name(field) == (None, "Named-No-Email")

    def test_null_email_falls_through(self):
        field = {"EMail": None, "Email": "second@x.com", "Title": "T"}
        assert _extract_user_field_email_name(field) == ("second@x.com", "T")


class TestCorpUserProfileDict:
    """Corp SP UserProfile expansion (2026-07-27 architect screenshot):
    Projects_MMK TPM column returns keys 'Work email' (space) + 'Name'
    (Distinguished-Name shape with `/` delimiters); 'Title' is empty in
    real traffic. The extractor must reach 'Work email' when 'EMail' is
    absent, and produce a clean display name from the DN head."""

    def test_work_email_with_space_key(self):
        # Verbatim shape from the 2026-07-27 screenshot
        field = {
            "Account":    "i:0#.w|corp\\t.arasu",
            "Name":       "Thendral Arasu Panneer Selvam/Device Management /MNOs Lab./Senior Professional/Samsung Electronics",
            "Work email": "t.arasu@samsung.com",
            "Department": "Device Management /MNOs Lab.",
            "Title":      "",
            "First name": "Thendral Arasu",
            "Last name":  "Panneer Selvam",
            "User name":  "t.arasu",
        }
        email, name = _extract_user_field_email_name(field)
        assert email == "t.arasu@samsung.com"
        # Title is empty; Name has DN slashes -> head component is the display name
        assert name == "Thendral Arasu Panneer Selvam"

    def test_workemail_camelcase_variant(self):
        # UserProfile JSON typically strips the space
        field = {"WorkEmail": "t.arasu@samsung.com", "Title": "TA"}
        assert _extract_user_field_email_name(field) == (
            "t.arasu@samsung.com", "TA",
        )

    def test_title_wins_over_name_when_both_present(self):
        # If Title is populated, use it directly (don't touch Name)
        field = {"Work email": "t@x.com", "Title": "T A", "Name": "Full/DN/Value"}
        assert _extract_user_field_email_name(field) == ("t@x.com", "T A")

    def test_first_plus_last_fallback_when_all_display_names_missing(self):
        field = {
            "Work email": "t@x.com",
            "First name": "Thendral Arasu",
            "Last name":  "Panneer Selvam",
        }
        assert _extract_user_field_email_name(field) == (
            "t@x.com", "Thendral Arasu Panneer Selvam",
        )

    def test_name_dn_with_no_slash_returned_as_is(self):
        field = {"Work email": "t@x.com", "Name": "Just A Name"}
        assert _extract_user_field_email_name(field) == ("t@x.com", "Just A Name")

    def test_dn_head_trimmed(self):
        # Whitespace around the head segment is stripped
        field = {"Work email": "t@x.com", "Name": "  Head Name  /rest"}
        assert _extract_user_field_email_name(field) == ("t@x.com", "Head Name")


class TestListOfUserDicts:
    """Multi-user SP field: list of user dicts. Take the first with an email."""

    def test_first_entry_with_email_wins(self):
        field = [
            {"EMail": "first@x.com", "Title": "First"},
            {"EMail": "second@x.com", "Title": "Second"},
        ]
        assert _extract_user_field_email_name(field) == ("first@x.com", "First")

    def test_skips_leading_empty_entries(self):
        field = [
            {"EMail": None, "Title": "No Email User"},
            {"EMail": "real@x.com", "Title": "Real User"},
        ]
        assert _extract_user_field_email_name(field) == ("real@x.com", "Real User")

    def test_all_empty_list_returns_none(self):
        field = [{}, {"Title": "no-email-1"}, {"Title": "no-email-2"}]
        assert _extract_user_field_email_name(field) == (None, None)


class TestUnexpectedTypes:
    def test_int_returns_none(self):
        # SP User field without $expand returns just an int Id — we can't
        # resolve email from an id alone; caller sees miss and skips send.
        assert _extract_user_field_email_name(42) == (None, None)

    def test_bytes_returns_none(self):
        assert _extract_user_field_email_name(b"t.arasu@samsung.com") == (None, None)
