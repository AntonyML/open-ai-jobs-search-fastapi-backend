"""Salary benchmark service — wraps the source repo's salary_lookup.py.

The original tool is stdlib-only Python and reads salary_data.json from
its own directory.  This package re-exports its public functions and
provides an async wrapper for use from FastAPI services.

Data file location: app/services/salary/salary_data.json (gitignored).
"""

from app.services.salary.salary_lookup import (  # noqa: F401
    anglicize,
    extract_core_words,
    format_entry,
    load_data,
    match_score,
    normalize,
    search_company,
)
