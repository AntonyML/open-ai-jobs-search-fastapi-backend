"""Excel-to-JSON converter for salary data (copied from source repo).

Re-exports the public helpers from convert_salary_excel.py.

The script's CLI entry point is `convert_salary_excel.main()` — call it
directly if you need to invoke it programmatically.
"""

from app.services.salary.tools.convert_salary_excel import (  # noqa: F401
    CITY_PATTERNS,
    COMPANY_PATTERNS,
    COMPOUND_PATTERNS,
    COUNT_PATTERNS,
    INDEX_PATTERNS,
    detect_column_type,
    header_matches,
    parse_sheet,
    strip_type_patterns,
)
