"""Add-portal service — generates a job portal search skill.

Implements the /add-portal workflow from the original repo:
1. Investigates the portal (endpoints, parameters, HTML/JSON structure)
2. Scaffolds a Bun/TypeScript CLI skill from the canonical template
3. Test-runs a live query before registering
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.settings import get_settings
from app.db.models import User
from app.exceptions import LLMError, NotFoundError
from app.llm.adapter import llm_completion_structured
from app.schemas.add_portal import (
    AddPortalRequest,
    PortalInvestigationLLMOutput,
    PortalSkillGenerationLLMOutput,
)

settings = get_settings()

# ── Guardrail constant ──────────────────────────────────────────────

ADD_PORTAL_GUARDRAIL = """
IMPORTANT GUARDRAIL: You are generating a job portal search skill.
You MUST follow the canonical structure exactly as specified.
The generated skill MUST:
- Use zero runtime dependencies (plain Bun + fetch)
- Follow the exact CLI contract: search + detail commands
- Output JSON to stdout, errors to stderr
- Use browser User-Agent and exponential backoff
- Handle 404/429/5xx gracefully
- Parse HTML with chunked regex (not full DOM)
- Include test helpers and smoke test
"""

# ── Canonical template paths ────────────────────────────────────────

TEMPLATE_DIR = Path(settings.scrapers_dir).parent / "templates" / "portal-skill-template"


# ── Prompt builders ─────────────────────────────────────────────────


def build_investigation_prompt(portal_url: str) -> list[dict[str, str]]:
    """Build prompt for investigating a job portal."""
    system_prompt = f"""{ADD_PORTAL_GUARDRAIL}

You are investigating a job portal to understand its search and detail APIs.

PORTAL URL: {portal_url}

TASK:
Investigate the portal and return structured information about:
1. Search endpoint and parameters
2. Detail endpoint and parameters
3. Result field mappings
4. Access requirements (auth, robots.txt, terms)

Return ONLY valid JSON matching the PortalInvestigationLLMOutput schema.
"""

    user_prompt = f"Investigate {portal_url} and return the structured investigation results."

    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]


def build_generation_prompt(
    portal_url: str,
    skill_name: str,
    market_and_language: str,
    test_query: str,
    investigation: dict[str, Any],
) -> list[dict[str, str]]:
    """Build prompt for generating the portal skill code."""
    system_prompt = f"""{ADD_PORTAL_GUARDRAIL}

You are generating a Bun/TypeScript job portal search skill.

PORTAL: {portal_url}
SKILL NAME: {skill_name}
MARKET/LANGUAGE: {market_and_language}
TEST QUERY: {test_query}

INVESTIGATION RESULTS:
{json.dumps(investigation, indent=2)}

TASK:
Generate the complete skill code following the canonical structure.
The skill must be placed in .agents/skills/{skill_name}/cli/

REQUIRED FILES:
1. package.json - name: {skill_name}-cli, type: module, scripts: start, test, typecheck
2. tsconfig.json - strict, ESNext, moduleResolution: bundler
3. README.md - with trigger phrases, personal-use warning if needed, command reference, examples
4. src/cli.ts - arg parsing, help text, command dispatch
5. src/helpers.ts - fetch with backoff, parsers, error writer
6. src/commands/search.ts - search command implementation
7. src/commands/detail.ts - detail command implementation
8. tests/helpers.ts - runCLI + parseJSON test utilities
9. tests/commands.test.ts - smoke test

Return ONLY valid JSON matching the PortalSkillGenerationLLMOutput schema.
"""

    user_prompt = f"Generate the complete skill code for {skill_name}."

    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]


# ── Main orchestration ──────────────────────────────────────────────


async def execute_add_portal(
    db: AsyncSession,
    user_id: str,
    payload: AddPortalRequest,
) -> dict[str, Any]:
    """Execute the add-portal workflow.

    Args:
        db: Database session
        user_id: Authenticated user ID
        payload: AddPortalRequest with portal details

    Returns:
        Dict with skill metadata and status
    """
    # 1. Verify user exists
    user_result = await db.execute(select(User).where(User.id == user_id))
    user = user_result.scalar_one_or_none()
    if user is None:
        raise NotFoundError("User not found.")

    # 2. Check if skill name already exists
    skill_dir = Path(settings.scrapers_dir) / payload.skill_name
    if skill_dir.exists():
        raise ValueError(f"Skill '{payload.skill_name}' already exists.")

    # 3. Investigate the portal
    investigation = await _investigate_portal(payload.portal_url)

    # 4. Generate skill code via LLM
    skill_code = await _generate_skill_code(
        portal_url=payload.portal_url,
        skill_name=payload.skill_name,
        market_and_language=payload.market_and_language,
        test_query=payload.test_query,
        investigation=investigation,
    )

    # 5. Write skill files
    await _write_skill_files(payload.skill_name, skill_code)

    # 6. Test-run the skill
    test_result = await _test_skill(payload.skill_name, payload.test_query)

    # 7. Return result
    now = datetime.now(timezone.utc)
    return {
        "skill_name": payload.skill_name,
        "portal_url": payload.portal_url,
        "market_and_language": payload.market_and_language,
        "test_query": payload.test_query,
        "status": "completed" if test_result["success"] else "completed_with_warnings",
        "test_result": test_result,
        "investigation": investigation,
        "created_at": now,
        "updated_at": now,
    }


async def _investigate_portal(portal_url: str) -> dict[str, Any]:
    """Investigate a job portal using LLM + web search."""
    messages = build_investigation_prompt(portal_url)

    try:
        result: PortalInvestigationLLMOutput = await llm_completion_structured(
            messages=messages,
            output_schema=PortalInvestigationLLMOutput,
            provider=settings.llm_default_provider,
            temperature=0.3,
            max_tokens=3000,
        )
        return result.model_dump()
    except Exception as e:
        raise LLMError(f"Portal investigation failed: {e}") from e


async def _generate_skill_code(
    portal_url: str,
    skill_name: str,
    market_and_language: str,
    test_query: str,
    investigation: dict[str, Any],
) -> dict[str, Any]:
    """Generate the complete skill code via LLM."""
    messages = build_generation_prompt(
        portal_url=portal_url,
        skill_name=skill_name,
        market_and_language=market_and_language,
        test_query=test_query,
        investigation=investigation,
    )

    try:
        result: PortalSkillGenerationLLMOutput = await llm_completion_structured(
            messages=messages,
            output_schema=PortalSkillGenerationLLMOutput,
            provider=settings.llm_default_provider,
            temperature=0.2,
            max_tokens=8000,
        )
        return result.model_dump()
    except Exception as e:
        raise LLMError(f"Skill code generation failed: {e}") from e


async def _write_skill_files(skill_name: str, skill_code: dict[str, Any]) -> None:
    """Write generated skill files to disk."""
    skill_dir = Path(settings.scrapers_dir) / skill_name
    cli_dir = skill_dir / "cli"
    src_dir = cli_dir / "src"
    commands_dir = src_dir / "commands"
    tests_dir = cli_dir / "tests"

    # Create directories
    for d in [skill_dir, cli_dir, src_dir, commands_dir, tests_dir]:
        d.mkdir(parents=True, exist_ok=True)

    # Write files
    files = {
        cli_dir / "package.json": skill_code["package_json"],
        cli_dir / "tsconfig.json": skill_code["tsconfig_json"],
        cli_dir / "README.md": skill_code["readme_md"],
        src_dir / "cli.ts": skill_code["cli_ts"],
        src_dir / "helpers.ts": skill_code["helpers_ts"],
        commands_dir / "search.ts": skill_code["search_ts"],
        commands_dir / "detail.ts": skill_code["detail_ts"],
        tests_dir / "helpers.ts": skill_code["test_helpers_ts"],
        tests_dir / "commands.test.ts": skill_code.get("test_commands_ts", ""),
    }

    for path, content in files.items():
        path.write_text(content, encoding="utf-8")


async def _test_skill(skill_name: str, test_query: str) -> dict[str, Any]:
    """Test-run the generated skill with a live query."""
    skill_dir = Path(settings.scrapers_dir) / skill_name / "cli"

    try:
        # Install dev dependencies
        install_proc = await asyncio.create_subprocess_exec(
            "bun", "install",
            cwd=str(skill_dir),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await install_proc.communicate()

        if install_proc.returncode != 0:
            return {"success": False, "error": "bun install failed"}

        # Typecheck
        typecheck_proc = await asyncio.create_subprocess_exec(
            "bun", "run", "typecheck",
            cwd=str(skill_dir),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await typecheck_proc.communicate()

        # Run search with test query
        search_proc = await asyncio.create_subprocess_exec(
            "bun", "run", "src/cli.ts", "search", "-q", test_query, "--limit", "3", "--format", "json",
            cwd=str(skill_dir),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await search_proc.communicate()

        if search_proc.returncode != 0:
            return {
                "success": False,
                "error": stderr.decode("utf-8", errors="replace"),
            }

        # Parse JSON output
        try:
            output = json.loads(stdout.decode("utf-8"))
            results = output.get("results", [])
            return {
                "success": True,
                "results_count": len(results),
                "sample_result": results[0] if results else None,
            }
        except json.JSONDecodeError:
            return {
                "success": False,
                "error": "Invalid JSON output",
            }

    except Exception as e:
        return {"success": False, "error": str(e)}


# ── Query helpers ───────────────────────────────────────────────────


async def get_portal_skill(
    db: AsyncSession, skill_name: str, user_id: str
) -> dict[str, Any]:
    """Get a portal skill by name (from filesystem)."""
    skill_dir = Path(settings.scrapers_dir) / skill_name
    if not skill_dir.exists():
        raise NotFoundError(f"Portal skill '{skill_name}' not found.")

    # Read package.json for metadata
    package_json = (Path(settings.scrapers_dir) / skill_name / "cli" / "package.json")
    metadata = {}
    if package_json.exists():
        metadata = json.loads(package_json.read_text(encoding="utf-8"))

    return {
        "skill_name": skill_name,
        "portal_url": metadata.get("description", ""),
        "market_and_language": "",
        "status": "installed",
        "created_at": datetime.fromtimestamp(skill_dir.stat().st_ctime, tz=timezone.utc),
        "updated_at": datetime.fromtimestamp(skill_dir.stat().st_mtime, tz=timezone.utc),
    }


async def list_portal_skills(
    db: AsyncSession,
    user_id: str,
    limit: int = 20,
    offset: int = 0,
) -> list[dict[str, Any]]:
    """List all installed portal skills."""
    scrapers_dir = Path(settings.scrapers_dir)
    if not scrapers_dir.exists():
        return []

    skills = []
    for skill_dir in sorted(scrapers_dir.iterdir()):
        if skill_dir.is_dir() and (skill_dir / "cli" / "package.json").exists():
            package_json = skill_dir / "cli" / "package.json"
            metadata = json.loads(package_json.read_text(encoding="utf-8"))
            skills.append({
                "skill_name": skill_dir.name,
                "portal_url": metadata.get("description", ""),
                "market_and_language": "",
                "status": "installed",
                "created_at": datetime.fromtimestamp(skill_dir.stat().st_ctime, tz=timezone.utc),
            })

    return skills[offset:offset + limit]