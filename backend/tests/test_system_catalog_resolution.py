"""Tests for template matching in the system catalog resolution service."""
import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.system_catalog_resolution import match_template


def _template(**kwargs):
    defaults = dict(
        id=1, slug="github", name="GitHub", vendor="GitHub, Inc.",
        system_type="code_repository",
        aliases=["github", "github enterprise", "github enterprise cloud"],
        is_fallback=False, organization_id=None,
    )
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


GITHUB = _template()
AWS = _template(
    id=2, slug="aws", name="Amazon Web Services", vendor="Amazon Web Services, Inc.",
    system_type="cloud_provider", aliases=["aws", "amazon web services", "aws cloudtrail"],
)
GITLAB = _template(
    id=3, slug="gitlab", name="GitLab", vendor="GitLab Inc.",
    system_type="code_repository", aliases=["gitlab"],
)
FALLBACK = _template(id=4, slug="fallback-code-repository", name="Generic Code Repository",
                     vendor="Generic", aliases=[], is_fallback=True)
ORG_PRIVATE = _template(id=5, slug="org-x-bespoke", name="Bespoke Tool", vendor="Acme",
                        aliases=["bespoke tool"], organization_id="some-org-uuid")

TEMPLATES = [GITHUB, AWS, GITLAB, FALLBACK, ORG_PRIVATE]


class TestMatchTemplate:
    def test_exact_alias_match_on_name(self):
        t = match_template(TEMPLATES, name="GitHub Enterprise", vendor=None,
                           system_type="code_repository")
        assert t is GITHUB

    def test_vendor_substring_match(self):
        t = match_template(TEMPLATES, name="Prod Cloud Account",
                           vendor="Amazon Web Services", system_type="cloud_provider")
        assert t is AWS

    def test_no_match_for_unknown_system(self):
        t = match_template(TEMPLATES, name="Bespoke Inventory Tool", vendor="HomeGrown Ltd",
                           system_type="custom")
        assert t is None

    def test_fallback_templates_never_matched(self):
        t = match_template(TEMPLATES, name="Generic Code Repository", vendor="Generic",
                           system_type="code_repository")
        assert t is None

    def test_org_private_templates_never_matched(self):
        t = match_template(TEMPLATES, name="Bespoke Tool", vendor="Acme",
                           system_type="custom")
        assert t is None

    def test_short_names_do_not_junk_match(self):
        # "Git" is a substring of GitHub/GitLab but too short to be a safe match
        t = match_template(TEMPLATES, name="Git", vendor=None, system_type="code_repository")
        assert t is None

    def test_cross_type_vendor_substring_blocked(self):
        # A shared vendor word must not link an unrelated product across
        # types: "Microsoft Teams" (communication) must not match an
        # identity_provider template vendored by "Microsoft Corporation"
        entra = _template(
            id=20, slug="microsoft-entra-id", name="Microsoft Entra ID",
            vendor="Microsoft Corporation", system_type="identity_provider",
            aliases=["entra", "entra id", "azure ad"],
        )
        t = match_template([entra], name="Microsoft Teams", vendor="Microsoft",
                           system_type="communication")
        assert t is None

    def test_same_type_vendor_substring_still_matches(self):
        entra = _template(
            id=20, slug="microsoft-entra-id", name="Microsoft Entra ID",
            vendor="Microsoft Corporation", system_type="identity_provider",
            aliases=["entra", "entra id", "azure ad"],
        )
        t = match_template([entra], name="Corp Entra ID Tenant", vendor="Microsoft",
                           system_type="identity_provider")
        assert t is entra

    def test_same_type_preferred_on_tie(self):
        a = _template(id=10, slug="acme-a", name="Acme Platform", vendor="Acme Corp",
                      aliases=["acme"], system_type="logging")
        b = _template(id=11, slug="acme-b", name="Acme Platform", vendor="Acme Corp",
                      aliases=["acme"], system_type="ticketing")
        t = match_template([a, b], name="Acme Platform", vendor="Acme Corp",
                           system_type="ticketing")
        assert t is b

    def test_case_insensitive(self):
        t = match_template(TEMPLATES, name="GITHUB", vendor=None,
                           system_type="code_repository")
        assert t is GITHUB
