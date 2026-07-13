"""Reset service — clears candidate profile data and career documents.

Implements the /reset workflow:
1. Wipes CandidateProfile, BehavioralProfile, and StarExamples from the database (profile scope).
2. Clears files under documents/ cv, linkedin, diplomas, references, applications (documents scope).
3. Requires confirmation token 'RESET' or raises ConfirmationRequiredError.
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.settings import get_settings
from app.db.models import BehavioralProfile, CandidateProfile, StarExample, User
from app.exceptions import ConfirmationRequiredError, NotFoundError

settings = get_settings()


# ── Helper functions ────────────────────────────────────────────────


def _get_documents_dir() -> Path:
    """Get the documents directory path."""
    if hasattr(settings, "documents_dir"):
        return Path(settings.documents_dir)
    # Default to "documents" in the parent workspace directory
    return Path(__file__).resolve().parent.parent.parent / "documents"


async def _get_profile_preview(db: AsyncSession, user_id: str) -> tuple[str, bool]:
    """Generate preview message for profile reset and check if there is content."""
    # Check CandidateProfile
    prof_res = await db.execute(select(CandidateProfile).where(CandidateProfile.user_id == user_id))
    profile = prof_res.scalar_one_or_none()
    
    if not profile:
        return "Profile data is already empty.", False
        
    # Check BehavioralProfile
    bp_res = await db.execute(select(BehavioralProfile).where(BehavioralProfile.candidate_id == profile.id))
    bp = bp_res.scalar_one_or_none()
    
    # Check StarExamples
    star_res = await db.execute(select(StarExample).where(StarExample.candidate_id == profile.id))
    stars = list(star_res.scalars().all())
    
    lines = [
        "## Profile reset will clear:",
        f"- Candidate Profile (ID: {profile.id}) — [has content]",
        f"- Behavioral Profile — [{'has content' if bp else 'already empty'}]",
        f"- STAR Examples — [{len(stars)} example(s) found]",
        "",
        "The CandidateProfile record will be deleted. Cascading foreign keys will automatically delete the associated BehavioralProfile and STAR Examples.",
    ]
    return "\n".join(lines), True


def _get_documents_preview() -> tuple[str, bool, list[Path]]:
    """List documents that will be deleted and check if any exist."""
    doc_dir = _get_documents_dir()
    if not doc_dir.exists():
        return "Documents folder does not exist — nothing to delete.", False, []

    subfolders = ["cv", "linkedin", "diplomas", "references", "applications"]
    lines = ["## Documents reset will delete:"]
    to_delete = []
    has_files = False

    for sf in subfolders:
        folder = doc_dir / sf
        lines.append(f"\n{sf}/")
        if not folder.exists():
            lines.append("  - (directory does not exist)")
            continue
            
        items = list(folder.iterdir())
        files = [f for f in items if f.is_file() and f.name != "README.md"]
        dirs = [d for d in items if d.is_dir()]
        
        if not files and not dirs:
            lines.append("  - (empty)")
        else:
            for f in files:
                lines.append(f"  - {f.name}")
                to_delete.append(f)
                has_files = True
            for d in dirs:
                lines.append(f"  - {d.name}/")
                to_delete.append(d)
                has_files = True

    lines.append("\nREADME.md — NOT deleted (instructions file)")
    return "\n".join(lines), has_files, to_delete


async def _reset_workspace_guidance_files() -> list[str]:
    """Locate and reset STAR examples and profile statements in workspace markdown files."""
    modified_files = []
    search_paths = [
        Path("E:/Dev/PoryectosDeTerceros/open-ai-jobs-search/FastAPI-backend"),
        Path("E:/Dev/PoryectosDeTerceros/open-ai-jobs-search"),
        Path("E:/Dev/PoryectosDeTerceros/ai-job-search"),
    ]

    # 1. 05-cv-templates.md
    for sp in search_paths:
        locs = [
            sp / ".claude" / "skills" / "job-application-assistant" / "05-cv-templates.md",
            sp / ".agents" / "skills" / "job-application-assistant" / "05-cv-templates.md",
        ]
        for loc in locs:
            if loc.exists() and loc.is_file():
                content = loc.read_text(encoding="utf-8")
                # Locate "**Profile statement templates" and replace it
                pattern = r"\*\*Profile statement templates:\*\*.*$"
                if re.search(pattern, content, re.DOTALL):
                    new_content = re.sub(
                        pattern,
                        "**Profile statement templates:**\n\n<!-- Run /setup to populate role-specific profile statements -->",
                        content,
                        flags=re.DOTALL,
                    )
                    loc.write_text(new_content, encoding="utf-8")
                    modified_files.append(f"05-cv-templates.md ({loc.parent.name})")
                    break

    # 2. 07-interview-prep.md
    for sp in search_paths:
        locs = [
            sp / ".claude" / "skills" / "job-application-assistant" / "07-interview-prep.md",
            sp / ".agents" / "skills" / "job-application-assistant" / "07-interview-prep.md",
        ]
        for loc in locs:
            if loc.exists() and loc.is_file():
                content = loc.read_text(encoding="utf-8")
                # Remove ## Ready-Made STAR Examples and ## STAR Candidates
                pattern = r"## Ready-Made STAR Examples.*?(?=##|$)"
                if re.search(pattern, content, re.DOTALL):
                    replacement = "## Ready-Made STAR Examples\n\n<!-- Run /setup to populate STAR examples from your actual experience -->\n\n"
                    new_content = re.sub(pattern, replacement, content, flags=re.DOTALL)
                    
                    # Also try to remove STAR Candidates if they exist
                    pattern_candidates = r"## STAR Candidates \(Complete Manually\).*?(?=##|$)"
                    new_content = re.sub(pattern_candidates, "", new_content, flags=re.DOTALL)
                    
                    loc.write_text(new_content, encoding="utf-8")
                    modified_files.append(f"07-interview-prep.md ({loc.parent.name})")
                    break

    return modified_files


# ── Service Orchestration ───────────────────────────────────────────


async def execute_reset(
    db: AsyncSession,
    user_id: str,
    scope: str,
    confirm: str | None = None,
) -> dict[str, Any]:
    """Initiate or execute candidate data and document resetting."""
    # 1. Verify user exists
    user_result = await db.execute(select(User).where(User.id == user_id))
    if user_result.scalar_one_or_none() is None:
        raise NotFoundError("User not found.")

    if scope not in ("profile", "documents", "all"):
        raise ValueError("Invalid scope. Must be 'profile', 'documents', or 'all'.")

    # 2. Build preview of what will be reset
    preview_parts = []
    has_profile_content = False
    has_document_content = False
    docs_to_delete = []

    if scope in ("profile", "all"):
        profile_preview, has_profile_content = await _get_profile_preview(db, user_id)
        preview_parts.append(profile_preview)

    if scope in ("documents", "all"):
        docs_preview, has_document_content, docs_to_delete = _get_documents_preview()
        preview_parts.append(docs_preview)

    preview_message = "\n\n".join(preview_parts)

    # 3. Handle confirmation requirement
    if confirm != "RESET":
        # Raise ConfirmationRequiredError with the preview text as message
        detailed_message = (
            f"{preview_message}\n\n"
            "WARNING: This action is destructive and cannot be undone.\n"
            "To confirm, re-submit the request with confirm='RESET'."
        )
        raise ConfirmationRequiredError(detailed_message)

    # 4. Execute the Reset
    cleared = []
    unchanged = []

    # Profile reset execution
    if scope in ("profile", "all"):
        if has_profile_content:
            prof_res = await db.execute(select(CandidateProfile).where(CandidateProfile.user_id == user_id))
            profile = prof_res.scalar_one()
            
            # Delete associated BehavioralProfile if exists
            bp_res = await db.execute(select(BehavioralProfile).where(BehavioralProfile.candidate_id == profile.id))
            bp = bp_res.scalar_one_or_none()
            if bp:
                await db.delete(bp)

            # Delete associated StarExamples if any exist
            star_res = await db.execute(select(StarExample).where(StarExample.candidate_id == profile.id))
            stars = list(star_res.scalars().all())
            for star in stars:
                await db.delete(star)

            # Delete CandidateProfile
            await db.delete(profile)
            await db.flush()
            cleared.append("Database: CandidateProfile, BehavioralProfile, STAR Examples")
            
            # Reset workspace guidance files if found
            guidance_cleared = await _reset_workspace_guidance_files()
            cleared.extend(guidance_cleared)
        else:
            unchanged.append("Database profile data (already empty)")

    # Documents reset execution
    if scope in ("documents", "all"):
        if has_document_content and docs_to_delete:
            for item in docs_to_delete:
                if item.is_file():
                    item.unlink()
                elif item.is_dir():
                    shutil.rmtree(item)
            cleared.append("Files: documents/ subfolders cleared")
        else:
            unchanged.append("Documents folder (already empty or only contains README.md)")

    # 5. Build post-reset message
    messages = []
    if scope == "profile":
        messages.append(
            "Your candidate profile is now blank. Run `/setup` to repopulate it. "
            "The command auto-detects any files in your `documents/` folder and offers to read from there; "
            "otherwise it walks you through a CV import or interactive interview."
        )
    elif scope == "documents":
        messages.append(
            "The `documents/` folder is now empty. Add your career documents and run `/setup` "
            "to populate your profile. See `documents/README.md` for instructions on what to put where."
        )
    else:  # all
        messages.append(
            "Both your profile files and documents folder are now empty. Add documents to `documents/` "
            "(or skip and use the CV import / interview path), then run `/setup`."
        )

    return {
        "status": "success",
        "scope": scope,
        "cleared": cleared,
        "unchanged": unchanged,
        "message": "\n\n".join(messages),
    }
