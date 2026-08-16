"""Tests for the CVLinter (CAPA 3) and the directed retry plumbing."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.cv_linter import (
    FORBIDDEN_BULLET_OPENERS,
    MIN_BULLET_LENGTH,
    lint_cv,
)

# A real profile-like object (dict-backed) with the attributes the linter reads.
def _profile(**overrides):
    base = {
        "full_name": "Ana María Gutiérrez",
        "email": "ana.gutierrez@email.com",
        "phone": "+506 8888 1234",
        "experience": [
            {"title": "Data Analyst", "company": "Banco Nacional", "bullets": []},
            {"title": "Junior Analyst", "company": "Supermercados Maxi", "bullets": []},
        ],
        "education": [{"degree": "Bach. Estadística", "institution": "UCR"}],
    }
    base.update(overrides)
    return MagicMock(**base)


def _clean_cv():
    return {
        "cv": {
            "first_name": "Ana María",
            "last_name": "Gutiérrez",
            "email": "ana.gutierrez@email.com",
            "phone": "+506 8888 1234",
            "profile_statement": "Data analyst with 4+ years driving decisions for retail and banking.",
            "skills": [
                {"label": "Languages", "skills": [{"name": "Python", "proficiency": "advanced"}]},
                {"label": "Tools", "skills": [{"name": "Power BI"}]},
            ],
            "experience": [
                {
                    "title": "Data Analyst",
                    "company": "Banco Nacional",
                    "bullets": [
                        "Built automated SQL dashboards that cut monthly reporting time by 30 percent",
                        "Drove a 12 percent lift in promo ticket by reframing category purchase funnels",
                    ],
                }
            ],
            "education": [{"degree": "Bach. Estadística", "institution": "UCR"}],
        },
        "metadata": {"language": "es"},
    }


# ── Clean CV ───────────────────────────────────────────────────────────


def test_clean_cv_has_no_issues():
    assert lint_cv(_clean_cv(), _profile()) == []


# ── Placeholders ───────────────────────────────────────────────────────


def test_placeholder_in_header_name_flagged_with_fix():
    cv = _clean_cv()
    cv["cv"]["first_name"] = "[Your Name]"
    issues = lint_cv(cv, _profile())
    assert len(issues) == 1
    assert "[Your Name]" in issues[0]
    assert "Ana María Gutiérrez" in issues[0]  # the fix carries the real name


def test_placeholder_tbd_and_company_name_flagged():
    cv = _clean_cv()
    cv["cv"]["experience"][0]["company"] = "Company Name"
    cv["cv"]["profile_statement"] = "TBD — will write later."
    issues = lint_cv(cv, _profile())
    assert any("Company Name" in i for i in issues)
    assert any("TBD" in i for i in issues)


# ── Bullets ────────────────────────────────────────────────────────────


def test_short_bullet_flagged():
    cv = _clean_cv()
    cv["cv"]["experience"][0]["bullets"] = ["Short bullet"]
    issues = lint_cv(cv, _profile())
    assert len(issues) == 1
    assert f"only {len('Short bullet')} chars" in issues[0]


def test_generic_opener_flagged():
    cv = _clean_cv()
    cv["cv"]["experience"][0]["bullets"] = [
        "Responsible for building dashboards that helped the team ship faster"
    ]
    issues = lint_cv(cv, _profile())
    assert len(issues) == 1
    assert "responsible for" in issues[0]
    assert "past-tense action verb" in issues[0]


# ── Company cross-reference ────────────────────────────────────────────


def test_invented_company_flagged():
    cv = _clean_cv()
    cv["cv"]["experience"][0]["company"] = "Facebook"
    issues = lint_cv(cv, _profile())
    assert any('"Facebook"' in i and "not invent employers" in i for i in issues)


def test_company_with_legal_suffix_matches_profile():
    cv = _clean_cv()
    cv["cv"]["experience"][0]["company"] = "Banco Nacional S.A."
    issues = lint_cv(cv, _profile())
    assert issues == []  # normalized to 'banco nacional'


# ── ATS basics ─────────────────────────────────────────────────────────


def test_email_and_phone_mismatch_flagged():
    cv = _clean_cv()
    cv["cv"]["email"] = "invented@example.com"
    cv["cv"]["phone"] = "+1 555 0000"
    issues = lint_cv(cv, _profile())
    assert any("email" in i and "does not match" in i for i in issues)
    assert any("phone" in i and "does not match" in i for i in issues)


def test_empty_skill_group_flagged():
    cv = _clean_cv()
    cv["cv"]["skills"] = [{"label": "Tools", "skills": []}]
    issues = lint_cv(cv, _profile())
    assert any('"Tools" is empty' in i for i in issues)


def test_missing_education_flagged_when_profile_has_it():
    cv = _clean_cv()
    del cv["cv"]["education"]
    issues = lint_cv(cv, _profile())
    assert any("Education is missing" in i for i in issues)


# ── Robustness ─────────────────────────────────────────────────────────


def test_mock_profile_never_crashes():
    # MagicMock without configured attrs: all checks must degrade to no-ops.
    assert lint_cv(_clean_cv(), MagicMock()) == []


def test_missing_cv_section_is_safe():
    assert lint_cv({"cv": {}}, MagicMock()) == []


def test_non_dict_output_is_safe():
    assert lint_cv([], MagicMock()) == []


# ── Directed retry plumbing ────────────────────────────────────────────


def test_build_lint_retry_prompt_contains_issues_and_cv():
    from app.services.apply_json import build_lint_retry_prompt

    issues = ["Bullet 1 in Banco Nacional starts with \"responsible for\" — rewrite"]
    messages = build_lint_retry_prompt(_clean_cv(), issues)
    assert len(messages) == 2
    assert "Fix ONLY the following" in messages[1]["content"]
    assert "Banco Nacional" in messages[1]["content"]
    assert issues[0] in messages[1]["content"]


@pytest.mark.asyncio
async def test_directed_retry_skipped_when_clean():
    from app.services.apply_json import _lint_and_directed_retry

    with patch("app.services.apply_json._llm_json", new=AsyncMock()) as mock_llm:
        result = await _lint_and_directed_retry(
            _clean_cv(), _profile(), {"provider": "openai", "model": "gpt-4o"}
        )
    assert result == _clean_cv()
    mock_llm.assert_not_awaited()


@pytest.mark.asyncio
async def test_directed_retry_calls_llm_with_fix_instructions():
    from app.services.apply_json import _lint_and_directed_retry

    bad = _clean_cv()
    bad["cv"]["first_name"] = "[Your Name]"
    fixed = _clean_cv()

    async def fake_llm_json(messages, schema_type, provider_config, **kwargs):
        assert messages[1]["content"].startswith("The previous CV output had quality issues")
        assert "[Your Name]" in messages[1]["content"]
        return fixed

    with patch("app.services.apply_json._llm_json", new=fake_llm_json):
        result = await _lint_and_directed_retry(
            bad, _profile(), {"provider": "openai", "model": "gpt-4o"}
        )
    assert result == fixed


@pytest.mark.asyncio
async def test_directed_retry_keeps_original_on_llm_error():
    from app.services.apply_json import _lint_and_directed_retry

    bad = _clean_cv()
    bad["cv"]["first_name"] = "[Your Name]"

    async def failing_llm_json(messages, schema_type, provider_config, **kwargs):
        from app.exceptions import LLMError

        raise LLMError("boom")

    with patch("app.services.apply_json._llm_json", new=failing_llm_json):
        result = await _lint_and_directed_retry(
            bad, _profile(), {"provider": "openai", "model": "gpt-4o"}
        )
    assert result == bad  # graceful degradation, never a crash
