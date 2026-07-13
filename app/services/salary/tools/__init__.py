"""Excel-to-JSON converter for salary data (copied from source repo).

Re-exports the public helpers from convert_salary_excel.py.

The script's CLI entry point is `convert_salary_excel.main()` — call it
directly if you need to invoke it programmatically.
"""

from app.services.salary.tools.convert_salary_excel import (  # noqa: F401
    parse_sheet,
    detect_column_type,
    header_matches,
    strip_type_patterns,
    INDEX_PATTERNS,
    COUNT_PATTERNS,
    COMPANY_PATTERNS,
    CITY_PATTERNS,
    COMPOUND_PATTERNS,
)