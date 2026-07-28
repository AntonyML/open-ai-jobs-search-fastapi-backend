"""Add-template service — registers, tests, and activates custom LaTeX templates.

Implements the /add-template workflow:
1. Validates the LaTeX source and metadata.
2. Checks that the template compiles with the specified engine.
3. Verifies that the compiled PDF page count matches the limit.
4. Stores the template and manifest under external/templates/.
5. Activates the template (updating active_templates.json and writing guidance overrides).
"""

from __future__ import annotations

import asyncio
import json
import re
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.settings import get_settings
from app.db.models import User
from app.exceptions import NotFoundError

settings = get_settings()


# ── Dummy Data for Compilation Check ────────────────────────────────


DUMMY_REPLACEMENTS = {
    "[YOUR_NAME]": "John Doe",
    "[YOUR_EMAIL]": "john.doe@example.com",
    "[YOUR_PHONE]": "+1 (555) 123-4567",
    "[YOUR_LINKEDIN_URL]": "linkedin.com/in/johndoe",
    "[YOUR_GITHUB_URL]": "github.com/johndoe",
    "[First]": "John",
    "[Last]": "Doe",
    "[Your Address, City, Country]": "123 Main St, Springfield, USA",
    "[Degree]": "Bachelor of Science in Computer Science",
    "[Institution]": "State University",
    "[Period]": "2018 - 2022",
    "[Key Topics / Thesis]": "Software Engineering, Algorithms, Databases",
    "[Job Title]": "Software Engineer",
    "[Company]": "Tech Solutions Inc.",
    "[Location]": "San Francisco, CA",
    "[Start Date]": "2022-06",
    "[End Date]": "Present",
    "[Bullet 1]": "Developed and maintained web applications using FastAPI and React.",
    "[Bullet 2]": "Optimized database queries, reducing response times by 30%.",
    "[Bullet 3]": "Collaborated with cross-functional teams to deliver high-quality software.",
    "[Project Name]": "E-Commerce Platform",
    "[Project Description]": "A full-stack e-commerce application built with FastAPI and Next.js.",
    "[Language Name]": "English",
    "[Proficiency]": "Native",
    "[Award Title]": "Employee of the Month",
    "[Award Description]": "Recognized for outstanding contributions to the team.",
    "[Reference Name]": "Jane Smith",
    "[Reference Contact]": "jane.smith@example.com",
    "[Company Connection]": "I have been following your company's growth and am excited about your mission.",
    "[Personal Fit]": "My experience in backend development makes me a strong fit for this role.",
    "[Body paragraph 1]": "I am writing to express my interest in the Software Engineer position.",
    "[Body paragraph 2]": "In my previous role, I designed and implemented scalable API endpoints.",
    "[Body paragraph 3]": "I look forward to discussing how my skills align with your needs.",
}


# ── Helper functions ────────────────────────────────────────────────


def _get_template_type_dir(template_type: str) -> str:
    """Normalize template type to folder name."""
    if template_type.lower() in ("cv", "resume"):
        return "cv"
    elif template_type.lower() in ("cover_letter", "cover-letter", "coverletter"):
        return "cover_letters"
    else:
        raise ValueError("Invalid template type. Must be 'cv' or 'cover_letter'.")


def _get_active_templates_map() -> dict[str, str]:
    """Read the active templates map from disk."""
    active_json_path = Path(settings.templates_dir) / "active_templates.json"
    if not active_json_path.exists():
        return {"cv": "default", "cover_letter": "default"}
    try:
        return json.loads(active_json_path.read_text(encoding="utf-8"))
    except Exception:
        return {"cv": "default", "cover_letter": "default"}


def _write_active_templates_map(mapping: dict[str, str]) -> None:
    """Write the active templates map to disk."""
    Path(settings.templates_dir).mkdir(parents=True, exist_ok=True)
    active_json_path = Path(settings.templates_dir) / "active_templates.json"
    active_json_path.write_text(json.dumps(mapping, indent=2), encoding="utf-8")


async def _get_pdf_page_count(pdf_path: Path) -> int:
    """Get page count of a PDF using pdfinfo or pdftotext."""
    for cmd in [["pdfinfo", str(pdf_path)], ["pdftotext", str(pdf_path), "-"]]:
        try:
            loop = asyncio.get_running_loop()
            result = await loop.run_in_executor(
                None,
                lambda c=cmd: subprocess.run(c, capture_output=True, timeout=30),
            )
            stdout, stderr = result.stdout, result.stderr
            if result.returncode == 0:
                output = stdout.decode("utf-8", errors="replace")
                match = re.search(r"Pages:\s+(\d+)", output)
                if match:
                    return int(match.group(1))
                return output.count("\f") + 1
        except FileNotFoundError:
            continue
    return 1


async def _find_and_update_guidance_file(
    template_type: str,
    name: str,
    engine: str,
    fonts: str,
    page_limit: int,
    deactivate: bool = False,
) -> None:
    """Find and update the relevant markdown guidance file in the workspace."""
    filename = "05-cv-templates.md" if template_type == "cv" else "06-cover-letter-templates.md"
    
    # Look for the file in the workspace (including source repo if it is mounted/accessible)
    search_paths = [
        Path("E:/Dev/PoryectosDeTerceros/open-ai-jobs-search/FastAPI-backend"),
        Path("E:/Dev/PoryectosDeTerceros/open-ai-jobs-search"),
        Path("E:/Dev/PoryectosDeTerceros/ai-job-search"),
    ]
    
    target_file: Path | None = None
    for sp in search_paths:
        # Check commonly known locations
        locs = [
            sp / ".claude" / "skills" / "job-application-assistant" / filename,
            sp / ".agents" / "skills" / "job-application-assistant" / filename,
            sp / filename,
        ]
        for loc in locs:
            if loc.exists() and loc.is_file():
                target_file = loc
                break
        if target_file:
            break

    if not target_file:
        # If not found, skip writing guidance block
        return

    content = target_file.read_text(encoding="utf-8")
    
    # Define markers
    begin_marker = "<!-- BEGIN ACTIVE-TEMPLATE (managed by /add-template - do not edit by hand) -->"
    end_marker = "<!-- END ACTIVE-TEMPLATE -->"
    
    # Prepare override block
    if deactivate or name == "default":
        replacement = ""
    else:
        rel_skeleton = f"templates/{template_type}/{name}/template.tex"
        rel_manifest = f"templates/{template_type}/{name}/TEMPLATE.md"
        
        override_block = [
            begin_marker,
            f"> **Active template override: `{name}`**",
            ">",
            "> A custom template is active. Where this block conflicts with the stock guidance below, this block wins. Structural advice below (tailoring, page-budget, cutting rules) still applies.",
            ">",
            f"> - **Template skeleton:** `{rel_skeleton}` — use this as the structural reference instead of the stock template",
            f"> - **Manifest:** `{rel_manifest}` — read this for style rules and known pitfalls before drafting",
            f"> - **Compile with:** `{engine}` (not the engine named in the stock guidance below)",
            f"> - **Fonts:** {fonts}",
            f"> - **Page limit:** exactly {page_limit} page(s)",
            f"> - **Output file:** unchanged (cv/main_<company>.tex / cover_letters/cover_<company>_<role>.tex); copy any class/font files the template needs into the output directory, or reference them by relative path",
            end_marker,
        ]
        replacement = "\n".join(override_block) + "\n"

    # Replace the existing block or insert after H1
    pattern = re.escape(begin_marker) + r".*?" + re.escape(end_marker) + r"\n?"
    if re.search(pattern, content, re.DOTALL):
        new_content = re.sub(pattern, replacement, content, flags=re.DOTALL)
    else:
        # Insert after the first H1
        h1_match = re.search(r"^(#\s+.*?\n)", content, re.MULTILINE)
        if h1_match:
            insert_pos = h1_match.end()
            new_content = content[:insert_pos] + "\n" + replacement + content[insert_pos:]
        else:
            new_content = replacement + "\n" + content

    target_file.write_text(new_content, encoding="utf-8")


# ── Service Orchestration ───────────────────────────────────────────


async def execute_add_template(
    db: AsyncSession,
    user_id: str,
    req: Any,  # AddTemplateRequest (loosely typed to avoid circular import issues)
) -> dict[str, Any]:
    """Register, test compile, and activate a custom LaTeX template."""
    # 1. Verify user exists
    user_result = await db.execute(select(User).where(User.id == user_id))
    if user_result.scalar_one_or_none() is None:
        raise NotFoundError("User not found.")

    # 2. Normalize template type
    folder_type = _get_template_type_dir(req.template_type)

    # 3. Ensure templates directory exists
    templates_root = Path(settings.templates_dir)
    templates_root.mkdir(parents=True, exist_ok=True)

    template_dir = templates_root / folder_type / req.name
    template_dir.mkdir(parents=True, exist_ok=True)

    # 4. Write template.tex
    tex_path = template_dir / "template.tex"
    tex_path.write_text(req.latex_content, encoding="utf-8")

    # 5. Verify compilation
    scratch_tex = template_dir / "_compile_test.tex"
    scratch_pdf = template_dir / "_compile_test.pdf"

    # Fill placeholders for compiling check
    filled_content = req.latex_content
    for placeholder, val in DUMMY_REPLACEMENTS.items():
        filled_content = filled_content.replace(placeholder, val)

    scratch_tex.write_text(filled_content, encoding="utf-8")

    try:
        # Compile twice to resolve page counts/references
        for _ in range(2):
            loop = asyncio.get_running_loop()
            result = await loop.run_in_executor(
                None,
                lambda: subprocess.run(
                    [req.engine, "-interaction=nonstopmode", "-output-directory", str(template_dir), str(scratch_tex)],
                    capture_output=True,
                    timeout=120,
                ),
            )
            stdout, stderr = result.stdout, result.stderr

            if result.returncode != 0:
                error_output = (stdout + stderr).decode("utf-8", errors="replace")
                raise RuntimeError(
                    f"Template test compilation failed with {req.engine}: {error_output}"
                )

        if not scratch_pdf.exists():
            raise RuntimeError("PDF was not generated during template compile check.")

        # Check page count limit
        actual_pages = await _get_pdf_page_count(scratch_pdf)
        if actual_pages > req.page_limit:
            raise RuntimeError(
                f"Template exceeds page limit. Expected max {req.page_limit} pages, got {actual_pages}."
            )

    finally:
        # Clean up scratch files
        for f in template_dir.iterdir():
            if f.name.startswith("_compile_test."):
                try:
                    f.unlink()
                except Exception:
                    pass

    # 6. Create manifest TEMPLATE.md
    manifest_path = template_dir / "TEMPLATE.md"
    style_rules_md = "\n".join([f"- {r}" for r in req.style_rules]) if req.style_rules else "- None recorded"
    
    manifest_content = f"""# Template: {req.name}

- **Type:** {req.template_type}
- **Engine:** {req.engine}
- **Page limit:** {req.page_limit} page(s)
- **Fonts:** {req.fonts}
- **Class/packages:** standard

## Compile command

    cd <output dir> && {req.engine} -interaction=nonstopmode <file>.tex

## Style rules

{style_rules_md}

## Known pitfalls

- {req.known_pitfalls or "None recorded"}
"""
    manifest_path.write_text(manifest_content, encoding="utf-8")

    # 7. Auto-activate the template
    active_map = _get_active_templates_map()
    active_map[folder_type] = req.name
    _write_active_templates_map(active_map)

    # 8. Update workspace guidance files if present
    await _find_and_update_guidance_file(
        template_type=folder_type,
        name=req.name,
        engine=req.engine,
        fonts=req.fonts,
        page_limit=req.page_limit,
    )

    now = datetime.now(timezone.utc)
    return {
        "name": req.name,
        "template_type": folder_type,
        "engine": req.engine,
        "fonts": req.fonts,
        "style_rules": req.style_rules,
        "page_limit": req.page_limit,
        "known_pitfalls": req.known_pitfalls,
        "active": True,
        "created_at": now,
        "updated_at": now,
    }


async def execute_switch_template(
    db: AsyncSession,
    user_id: str,
    req: Any,  # SwitchTemplateRequest
) -> dict[str, Any]:
    """Switch active template or reset to default."""
    # 1. Verify user
    user_result = await db.execute(select(User).where(User.id == user_id))
    if user_result.scalar_one_or_none() is None:
        raise NotFoundError("User not found.")

    folder_type = _get_template_type_dir(req.template_type)

    if req.name == "default":
        # Remove active override
        active_map = _get_active_templates_map()
        active_map[folder_type] = "default"
        _write_active_templates_map(active_map)

        # Deactivate in workspace guidance
        await _find_and_update_guidance_file(
            template_type=folder_type,
            name="default",
            engine="",
            fonts="",
            page_limit=0,
            deactivate=True,
        )

        return {
            "name": "default",
            "template_type": folder_type,
            "message": f"Successfully reverted to stock {req.template_type} template.",
        }

    # Verify template exists
    template_dir = Path(settings.templates_dir) / folder_type / req.name
    manifest_path = template_dir / "TEMPLATE.md"
    if not template_dir.exists() or not manifest_path.exists():
        raise NotFoundError(f"Template '{req.name}' is not registered under '{folder_type}'.")

    # Read manifest to extract details
    manifest_text = manifest_path.read_text(encoding="utf-8")
    
    # Parse manifest fields
    engine = "lualatex"
    engine_match = re.search(r"-\s+\*\*Engine:\*\*\s*(.*)$", manifest_text, re.MULTILINE)
    if engine_match:
        engine = engine_match.group(1).strip()

    fonts = "system font"
    fonts_match = re.search(r"-\s+\*\*Fonts:\*\*\s*(.*)$", manifest_text, re.MULTILINE)
    if fonts_match:
        fonts = fonts_match.group(1).strip()

    page_limit = 2
    limit_match = re.search(r"-\s+\*\*Page limit:\*\*\s*(\d+)", manifest_text)
    if limit_match:
        page_limit = int(limit_match.group(1))

    # Activate
    active_map = _get_active_templates_map()
    active_map[folder_type] = req.name
    _write_active_templates_map(active_map)

    # Update workspace guidance
    await _find_and_update_guidance_file(
        template_type=folder_type,
        name=req.name,
        engine=engine,
        fonts=fonts,
        page_limit=page_limit,
    )

    return {
        "name": req.name,
        "template_type": folder_type,
        "engine": engine,
        "fonts": fonts,
        "page_limit": page_limit,
        "active": True,
        "message": f"Template '{req.name}' is now active for {folder_type}.",
    }


async def get_template(
    db: AsyncSession,
    name: str,
    template_type: str,
    user_id: str,
) -> dict[str, Any]:
    """Retrieve details of a registered template."""
    user_result = await db.execute(select(User).where(User.id == user_id))
    if user_result.scalar_one_or_none() is None:
        raise NotFoundError("User not found.")

    folder_type = _get_template_type_dir(template_type)
    template_dir = Path(settings.templates_dir) / folder_type / name
    manifest_path = template_dir / "TEMPLATE.md"

    if not template_dir.exists() or not manifest_path.exists():
        raise NotFoundError(f"Template '{name}' of type '{folder_type}' not found.")

    manifest_text = manifest_path.read_text(encoding="utf-8")
    
    # Parse fields
    engine = "lualatex"
    engine_match = re.search(r"-\s+\*\*Engine:\*\*\s*(.*)$", manifest_text, re.MULTILINE)
    if engine_match:
        engine = engine_match.group(1).strip()

    fonts = "system font"
    fonts_match = re.search(r"-\s+\*\*Fonts:\*\*\s*(.*)$", manifest_text, re.MULTILINE)
    if fonts_match:
        fonts = fonts_match.group(1).strip()

    page_limit = 2
    limit_match = re.search(r"-\s+\*\*Page limit:\*\*\s*(\d+)", manifest_text)
    if limit_match:
        page_limit = int(limit_match.group(1))

    # Style rules
    style_rules = []
    rules_section = re.search(r"## Style rules\s*\n(.*?)(?:\n##|$)", manifest_text, re.DOTALL)
    if rules_section:
        rules_text = rules_section.group(1)
        style_rules = [line.lstrip("- ").strip() for line in rules_text.strip().split("\n") if line.strip()]

    # Pitfalls
    known_pitfalls = None
    pitfalls_section = re.search(r"## Known pitfalls\s*\n(.*)$", manifest_text, re.DOTALL)
    if pitfalls_section:
        known_pitfalls = pitfalls_section.group(1).lstrip("- ").strip()
        if known_pitfalls == "None recorded":
            known_pitfalls = None

    active_map = _get_active_templates_map()
    active = active_map.get(folder_type) == name

    # Stat files for times
    stat = template_dir.stat()
    return {
        "name": name,
        "template_type": folder_type,
        "engine": engine,
        "fonts": fonts,
        "style_rules": style_rules,
        "page_limit": page_limit,
        "known_pitfalls": known_pitfalls,
        "active": active,
        "created_at": datetime.fromtimestamp(stat.st_ctime, tz=timezone.utc),
        "updated_at": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc),
    }


async def list_templates(
    db: AsyncSession,
    template_type: str | None,
    user_id: str,
) -> list[dict[str, Any]]:
    """List all registered templates, optionally filtered by type."""
    user_result = await db.execute(select(User).where(User.id == user_id))
    if user_result.scalar_one_or_none() is None:
        raise NotFoundError("User not found.")

    types_to_scan = []
    if template_type:
        types_to_scan = [_get_template_type_dir(template_type)]
    else:
        types_to_scan = ["cv", "cover_letters"]

    templates = []
    templates_root = Path(settings.templates_dir)
    active_map = _get_active_templates_map()

    for folder_type in types_to_scan:
        type_dir = templates_root / folder_type
        if not type_dir.exists():
            continue
        
        for t_dir in sorted(type_dir.iterdir()):
            if t_dir.is_dir() and (t_dir / "TEMPLATE.md").exists():
                try:
                    name = t_dir.name
                    details = await get_template(db, name, folder_type, user_id)
                    templates.append({
                        "name": name,
                        "template_type": folder_type,
                        "engine": details["engine"],
                        "fonts": details["fonts"],
                        "active": active_map.get(folder_type) == name,
                        "created_at": details["created_at"],
                    })
                except Exception:
                    continue

    return templates
