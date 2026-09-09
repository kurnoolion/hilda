"""URLPFX-1: HILDA is served under /hilda/*, and the app emits prefixed urls.

nginx strips the prefix before proxying, so route declarations stay bare and
the app matches unprefixed paths -- which is why every other dashboard test
runs with `url_prefix=""`, the configuration that matches driving the app
directly with no proxy in front.

That leaves the production behaviour uncovered by those tests, which is what
this file is for: with a prefix configured, everything the app hands BACK to
the browser must carry it. An unprefixed href or redirect target sends the
browser to `/browse/...`, outside `/hilda/`, where nginx proxies nothing --
a dead link that no route-level test can see.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from core.src.dashboard.app import build_app
from core.src.dashboard.config import DashboardConfig
from core.src.dashboard.url_prefix import (
    DEFAULT_URL_PREFIX,
    join,
    normalize,
)


class TestNormalize:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("/hilda", "/hilda"),
            ("hilda", "/hilda"),        # missing leading slash
            ("/hilda/", "/hilda"),      # trailing slash
            ("hilda/", "/hilda"),
            ("  /hilda  ", "/hilda"),   # env vars carry whitespace
            ("//hilda//", "/hilda"),
            ("", ""),
            ("/", ""),
            (None, ""),
        ],
    )
    def test_shapes(self, raw, expected):
        assert normalize(raw) == expected

    def test_trailing_slash_would_have_produced_a_protocol_relative_url(self):
        """`/hilda/` + `/browse/x` naively concatenated gives `/hilda//browse/x`.
        Browsers read a leading `//` as protocol-relative and resolve it
        against a different host, so this is normalised at the boundary."""
        assert join(normalize("/hilda/"), "/browse/x") == "/hilda/browse/x"
        assert "//" not in join(normalize("/hilda/"), "/browse/x")


class TestJoin:
    def test_prefixes_an_absolute_path(self):
        assert join("/hilda", "/browse/MMK/SM-1/P1/") == "/hilda/browse/MMK/SM-1/P1/"

    def test_empty_prefix_is_a_passthrough(self):
        assert join("", "/browse/x") == "/browse/x"

    def test_is_idempotent(self):
        """Guards against a double application during refactoring producing
        /hilda/hilda/browse/..., which would 404 rather than fail loudly."""
        once = join("/hilda", "/browse/x")
        assert join("/hilda", once) == once

    def test_bare_prefix_is_not_re_prefixed(self):
        assert join("/hilda", "/hilda") == "/hilda"

    def test_adds_a_missing_leading_slash(self):
        assert join("/hilda", "browse/x") == "/hilda/browse/x"

    def test_query_strings_survive(self):
        assert join("/hilda", "/browse/x/?outcome=routed&target=a-b") == (
            "/hilda/browse/x/?outcome=routed&target=a-b"
        )

    def test_a_similarly_named_path_is_still_prefixed(self):
        """`/hildaXYZ` is not under `/hilda/`, so it must be prefixed. A
        naive startswith(prefix) check would wrongly leave it alone."""
        assert join("/hilda", "/hildaXYZ/x") == "/hilda/hildaXYZ/x"


class TestConfig:
    def test_default_is_hilda(self):
        assert DashboardConfig().url_prefix == DEFAULT_URL_PREFIX == "/hilda"

    def test_config_normalizes(self):
        assert DashboardConfig(url_prefix="hilda/").url_prefix == "/hilda"

    def test_empty_is_honoured_not_replaced_by_the_default(self):
        """The other dashboard tests depend on this: passing "" must actually
        disable the prefix rather than fall back to /hilda."""
        assert DashboardConfig(url_prefix="").url_prefix == ""


def _client(prefix: str) -> TestClient:
    cfg = DashboardConfig(url_prefix=prefix, mock_auth=True, ph1_minimal=False)
    return TestClient(build_app(cfg), follow_redirects=False)


class TestEmittedRedirects:
    """Redirect targets are emitted to the browser, so they carry the prefix
    even though the route that produced them is declared bare."""

    def test_feedback_bare_scope_redirect_is_prefixed(self):
        r = _client("/hilda").get("/feedback/MMK/SM-A012U")
        assert r.status_code == 302
        assert r.headers["location"] == "/hilda/feedback/MMK/SM-A012U/DRR"

    def test_same_redirect_is_bare_when_no_prefix_configured(self):
        r = _client("").get("/feedback/MMK/SM-A012U")
        assert r.headers["location"] == "/feedback/MMK/SM-A012U/DRR"

    def test_the_route_itself_still_matches_unprefixed(self):
        """nginx strips /hilda, so the app must NOT serve the prefixed path.
        Pinning this stops someone 'fixing' the asymmetry by prefixing the
        route declarations, which would break the strip in production."""
        c = _client("/hilda")
        assert c.get("/feedback/MMK/SM-A012U").status_code == 302
        assert c.get("/hilda/feedback/MMK/SM-A012U").status_code == 404


class TestEmittedHrefs:
    def test_rendered_page_links_carry_the_prefix(self):
        r = _client("/hilda").get("/feedback/MMK/SM-A012U/DRR")
        assert r.status_code == 200
        assert 'action="/hilda/feedback/MMK/SM-A012U/DRR/submit"' in r.text
        assert 'href="/hilda/browse/MMK/SM-A012U/DRR/"' in r.text

    def test_no_link_escapes_the_prefix(self):
        """Any absolute app path in the HTML that is NOT under /hilda/ would
        leave the served subtree. Checked by pattern rather than by listing
        known links, so a link added later is covered too."""
        import re

        r = _client("/hilda").get("/feedback/MMK/SM-A012U/DRR")
        stray = re.findall(
            r'(?:href|action)="(/(?!hilda/)(?:browse|docs|dl|feedback|admin|milestone)[^"]*)"',
            r.text,
        )
        assert stray == [], f"unprefixed links emitted: {stray}"

    def test_links_are_bare_when_no_prefix_configured(self):
        r = _client("").get("/feedback/MMK/SM-A012U/DRR")
        assert 'action="/feedback/MMK/SM-A012U/DRR/submit"' in r.text


class TestJinjaGlobal:
    def test_url_prefix_is_exposed_to_every_template(self):
        """All 12 render sites share one Jinja2Templates instance, so the
        global is what makes the prefix reach templates in app.py,
        document_view_routes.py and feedback_routes.py alike."""
        app = build_app(DashboardConfig(url_prefix="/hilda", mock_auth=True))
        # build_app stores nothing, so re-derive via a render instead.
        r = TestClient(app, follow_redirects=False).get("/feedback/MMK/SM-A012U/DRR")
        assert "/hilda/" in r.text
