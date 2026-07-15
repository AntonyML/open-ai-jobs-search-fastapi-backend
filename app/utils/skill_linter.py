"""Skill Linter — validates candidate skills against a known dictionary.

Adapted from MadsLorentzen/ai-job-search tools/lint_skills.py.

Checks candidate profile skills for:
1. Known technology/skill terms (not typos)
2. Appropriate granularity (not too vague like "coding", "computers")
3. Consistent naming (prefers "Python" over "python", "py")
4. Common misspellings and alternative names

This is used during /setup to validate skills as the user enters them,
and can be called manually to clean up existing profiles.

The known_skills dictionary is seeded with common tech skills and can
be extended via the add_known_skills() function.

100% DETERMINISTIC — no LLM calls.
"""

from __future__ import annotations

import re
from typing import Any

# ── Known skills dictionary ─────────────────────────────────────────
# Format: {"canonical_name": {"aliases": [...], "category": "..."}}

KNOWN_SKILLS: dict[str, dict[str, Any]] = {
    # Programming Languages
    "Python": {"aliases": ["python", "py", "python3"], "category": "programming_language"},
    "JavaScript": {"aliases": ["javascript", "js", "ecmascript"], "category": "programming_language"},
    "TypeScript": {"aliases": ["typescript", "ts"], "category": "programming_language"},
    "Java": {"aliases": ["java", "java8", "java11", "java17"], "category": "programming_language"},
    "C++": {"aliases": ["c++", "cpp", "cplusplus"], "category": "programming_language"},
    "C#": {"aliases": ["c#", "csharp", "c-sharp"], "category": "programming_language"},
    "Go": {"aliases": ["go", "golang"], "category": "programming_language"},
    "Rust": {"aliases": ["rust", "rust-lang"], "category": "programming_language"},
    "Ruby": {"aliases": ["ruby", "rb"], "category": "programming_language"},
    "Swift": {"aliases": ["swift"], "category": "programming_language"},
    "Kotlin": {"aliases": ["kotlin", "kt"], "category": "programming_language"},
    "Scala": {"aliases": ["scala"], "category": "programming_language"},
    "R": {"aliases": ["r", "r-lang"], "category": "programming_language"},
    "SQL": {"aliases": ["sql", "pl/sql", "tsql", "t-sql"], "category": "query_language"},
    "HTML": {"aliases": ["html", "html5"], "category": "markup"},
    "CSS": {"aliases": ["css", "css3"], "category": "stylesheet"},
    "Bash": {"aliases": ["bash", "shell", "sh", "zsh"], "category": "scripting"},
    "MATLAB": {"aliases": ["matlab"], "category": "programming_language"},
    "Julia": {"aliases": ["julia"], "category": "programming_language"},

    # ML/DL Frameworks
    "PyTorch": {"aliases": ["pytorch", "torch", "libtorch"], "category": "ml_framework"},
    "TensorFlow": {"aliases": ["tensorflow", "tf", "tf2", "tf-keras"], "category": "ml_framework"},
    "scikit-learn": {"aliases": ["scikit-learn", "sklearn", "scikit learn"], "category": "ml_library"},
    "JAX": {"aliases": ["jax"], "category": "ml_framework"},
    "Keras": {"aliases": ["keras"], "category": "ml_framework"},
    "Hugging Face": {"aliases": ["hugging face", "huggingface", "transformers", "hf"], "category": "ml_library"},
    "LangChain": {"aliases": ["langchain", "lang-chain"], "category": "llm_framework"},
    "LlamaIndex": {"aliases": ["llamaindex", "llama-index"], "category": "llm_framework"},
    "OpenAI API": {"aliases": ["openai", "openai api", "gpt api"], "category": "llm_api"},
    "spaCy": {"aliases": ["spacy", "spacy 3"], "category": "nlp_library"},
    "NLTK": {"aliases": ["nltk"], "category": "nlp_library"},
    "XGBoost": {"aliases": ["xgboost", "xgb"], "category": "ml_library"},
    "LightGBM": {"aliases": ["lightgbm", "lgbm"], "category": "ml_library"},

    # Cloud & DevOps
    "AWS": {"aliases": ["aws", "amazon web services", "ec2", "s3", "lambda"], "category": "cloud"},
    "GCP": {"aliases": ["gcp", "google cloud", "google cloud platform"], "category": "cloud"},
    "Azure": {"aliases": ["azure", "microsoft azure"], "category": "cloud"},
    "Docker": {"aliases": ["docker", "docker-compose"], "category": "devops"},
    "Kubernetes": {"aliases": ["kubernetes", "k8s", "kube"], "category": "devops"},
    "Terraform": {"aliases": ["terraform", "tf"], "category": "iac"},
    "Ansible": {"aliases": ["ansible"], "category": "iac"},
    "CI/CD": {"aliases": ["ci/cd", "cicd", "continuous integration", "continuous deployment"], "category": "devops"},
    "GitHub Actions": {"aliases": ["github actions", "gh actions"], "category": "ci_cd"},
    "Jenkins": {"aliases": ["jenkins"], "category": "ci_cd"},
    "Git": {"aliases": ["git", "git scm"], "category": "version_control"},
    "Linux": {"aliases": ["linux", "unix", "ubuntu", "debian"], "category": "os"},

    # Data & Databases
    "PostgreSQL": {"aliases": ["postgresql", "postgres", "psql"], "category": "database"},
    "MySQL": {"aliases": ["mysql", "mariadb"], "category": "database"},
    "MongoDB": {"aliases": ["mongodb", "mongo"], "category": "database"},
    "Redis": {"aliases": ["redis"], "category": "cache"},
    "Elasticsearch": {"aliases": ["elasticsearch", "es", "elk"], "category": "search"},
    "Apache Spark": {"aliases": ["spark", "apache spark", "pyspark"], "category": "big_data"},
    "Apache Kafka": {"aliases": ["kafka", "apache kafka"], "category": "streaming"},
    "Airflow": {"aliases": ["airflow", "apache airflow"], "category": "orchestration"},
    "Snowflake": {"aliases": ["snowflake"], "category": "data_warehouse"},
    "dbt": {"aliases": ["dbt", "data build tool"], "category": "data_engineering"},
    "Pandas": {"aliases": ["pandas"], "category": "data_library"},
    "NumPy": {"aliases": ["numpy", "np"], "category": "data_library"},

    # Tools & Productivity
    "Jira": {"aliases": ["jira", "jira software"], "category": "project_management"},
    "Confluence": {"aliases": ["confluence"], "category": "documentation"},
    "Figma": {"aliases": ["figma"], "category": "design"},
    "VS Code": {"aliases": ["vscode", "vs code", "visual studio code"], "category": "ide"},
    "PyCharm": {"aliases": ["pycharm", "intellij"], "category": "ide"},
    "Postman": {"aliases": ["postman"], "category": "api_testing"},
    "Datadog": {"aliases": ["datadog", "dd"], "category": "monitoring"},

    # Domain Expertise
    "Machine Learning": {"aliases": ["machine learning", "ml"], "category": "domain"},
    "Deep Learning": {"aliases": ["deep learning", "dl", "neural networks"], "category": "domain"},
    "NLP": {"aliases": ["nlp", "natural language processing"], "category": "domain"},
    "Computer Vision": {"aliases": ["computer vision", "cv", "image processing"], "category": "domain"},
    "Recommendation Systems": {"aliases": ["recommendation systems", "recommender systems", "recsys"], "category": "domain"},
    "Reinforcement Learning": {"aliases": ["reinforcement learning", "rl", "deep rl"], "category": "domain"},
    "MLOps": {"aliases": ["mlops", "ml-ops", "machine learning operations"], "category": "domain"},
}

# Vague terms that should trigger a warning
VAGUE_SKILLS = {
    "coding", "programming", "computers", "computer", "it", "tech",
    "software", "hardware", "office", "microsoft office", "word", "excel",
    "powerpoint", "outlook", "internet", "email", "typing",
    "fast learner", "team player", "hard working", "dedicated",
    "problem solver", "good communication", "leadership",
}


# ── Lint result ─────────────────────────────────────────────────────


class SkillLintResult:
    """Result of a skill lint operation."""

    def __init__(self) -> None:
        self.errors: list[str] = []
        self.warnings: list[str] = []
        self.suggestions: list[tuple[str, str]] = []  # (original, suggested)

    @property
    def passed(self) -> bool:
        return len(self.errors) == 0

    @property
    def summary(self) -> str:
        parts = []
        if self.errors:
            parts.append(f"{len(self.errors)} error(s)")
        if self.warnings:
            parts.append(f"{len(self.warnings)} warning(s)")
        if self.suggestions:
            parts.append(f"{len(self.suggestions)} suggestion(s)")
        return ", ".join(parts) if parts else "All skills valid"


def add_known_skills(skills: dict[str, dict[str, Any]]) -> None:
    """Add custom skills to the known skills dictionary.

    Args:
        skills: Dict mapping canonical skill name to {aliases, category}.
    """
    KNOWN_SKILLS.update(skills)


def lint_skill(skill: str) -> SkillLintResult:
    """Lint a single skill name.

    Args:
        skill: The skill name to validate.

    Returns:
        SkillLintResult with errors, warnings, and suggestions.
    """
    result = SkillLintResult()
    skill = skill.strip()
    skill_lower = skill.lower()

    if not skill_lower:
        result.errors.append("Skill name is empty")
        return result

    # Check for vague terms
    if skill_lower in VAGUE_SKILLS:
        result.warnings.append(f"'{skill}' is too vague — use a more specific skill")

    # Check length
    if len(skill) > 100:
        result.errors.append(f"Skill name too long ({len(skill)} chars)")

    if len(skill) < 2:
        result.errors.append(f"Skill name too short")
        return result

    # Resolve alias (handles lowercase "pytorch" → "PyTorch", "sklearn" → "scikit-learn")
    canonical = _resolve_alias(skill)
    if canonical and canonical != skill:
        result.suggestions.append((skill, canonical))

    # If no canonical match, try fuzzy matching for potential typos
    if not canonical:
        best_match = _find_closest_known_skill(skill_lower)
        if best_match and best_match.lower() != skill_lower:
            result.suggestions.append((skill, best_match))

    # Check for special characters
    if re.search(r"[^a-zA-Z0-9+#.\-/\s]", skill):
        result.warnings.append(f"'{skill}' contains unusual characters")

    return result


def lint_skills_list(skills: list[str]) -> SkillLintResult:
    """Lint a list of skills.

    Args:
        skills: List of skill names to validate.

    Returns:
        Aggregate SkillLintResult.
    """
    result = SkillLintResult()
    for skill in skills:
        single = lint_skill(skill)
        result.errors.extend(single.errors)
        result.warnings.extend(single.warnings)
        result.suggestions.extend(single.suggestions)
    return result


def lint_skills_dict(skills_dict: dict[str, Any]) -> SkillLintResult:
    """Lint skills from a candidate profile's skills dict.

    Handles the nested structure:
    ```python
    {
        "programming_ml": [{"language": "Python", ...}],
        "domain_expertise": ["Machine Learning", ...],
        "software_tools": ["Docker", ...],
    }
    ```

    Args:
        skills_dict: The skills dict from CandidateProfile.skills.

    Returns:
        Aggregate SkillLintResult.
    """
    result = SkillLintResult()

    all_skills: list[str] = []

    # Extract from programming_ml (list of dicts with 'language' key)
    for item in skills_dict.get("programming_ml", []):
        if isinstance(item, dict):
            lang = item.get("language", "")
            if lang:
                all_skills.append(lang)
            for fw in item.get("frameworks", []):
                if fw:
                    all_skills.append(fw)
        elif isinstance(item, str):
            all_skills.append(item)

    # Extract from domain_expertise (list of strings)
    for item in skills_dict.get("domain_expertise", []):
        if item:
            all_skills.append(item)

    # Extract from software_tools (list of strings)
    for item in skills_dict.get("software_tools", []):
        if item:
            all_skills.append(item)

    # Lint each skill
    for skill in all_skills:
        single = lint_skill(skill)
        result.errors.extend(single.errors)
        result.warnings.extend(single.warnings)
        result.suggestions.extend(single.suggestions)

    return result


def _resolve_alias(skill: str) -> str | None:
    """Resolve a skill alias to its canonical name.

    Args:
        skill: The skill name to resolve.

    Returns:
        Canonical skill name, or None if not found.
    """
    skill_lower = skill.strip().lower()
    for canonical, info in KNOWN_SKILLS.items():
        if canonical.lower() == skill_lower:
            return canonical
        for alias in info.get("aliases", []):
            if alias.lower() == skill_lower:
                return canonical
    return None


def _find_closest_known_skill(skill: str) -> str | None:
    """Find the closest known skill by simple substring matching.

    This is a lightweight alternative to full Levenshtein distance.

    Args:
        skill: Lowercased skill name.

    Returns:
        Closest canonical skill name, or None.
    """
    skill = skill.strip().lower()
    if not skill:
        return None

    # Exact match (check aliases and canonical names)
    for canonical, info in KNOWN_SKILLS.items():
        if canonical.lower() == skill:
            return canonical
        for alias in info.get("aliases", []):
            if alias.lower() == skill:
                return canonical

    # Substring match: known skill appears in the input
    # e.g. "pytorch" → "PyTorch"
    for canonical, info in KNOWN_SKILLS.items():
        if canonical.lower() in skill or skill in canonical.lower():
            return canonical
        for alias in info.get("aliases", []):
            if alias.lower() in skill or skill in alias.lower():
                return canonical

    return None
