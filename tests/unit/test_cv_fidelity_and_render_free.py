"""Unit tests for CV Fidelity, 100% data pass-through, and Render Free resilience."""

import pytest
from app.db.models import CandidateProfile, User
from app.services.apply_json import _build_candidate_summary
from app.services.cv_linter import lint_cv


def test_build_candidate_summary_includes_all_fields():
    """Verify _build_candidate_summary does not truncate and includes all sections."""
    user = User(
        id="user-123",
        full_name="Antony Jafeth Monge López",
        email="antonyml2016@gmail.com",
    )
    profile = CandidateProfile(
        id="prof-123",
        user_id="user-123",
        location="Cartago, Costa Rica",
        phone="+506 8545-6150",
        linkedin_url="https://linkedin.com/in/antony-monge-lopez",
        github_url="https://github.com/AntonyML",
        portfolio_url="https://tonyml.com",
        profile_statement="Software Engineer with experience in .NET and SQL Server.",
        education=[
            {"degree": "Bachelor of Computer Engineering", "institution": "Universidad de Costa Rica", "start_date": "2021", "end_date": "2026"},
            {"degree": "Middle Technician in Software Development", "institution": "CTP Umberto Melloni", "start_date": "2014", "end_date": "2020"},
        ],
        experience=[
            {
                "title": "Full-Stack Developer",
                "company": "Freelance",
                "location": "Remote",
                "client_context": "Armería - Rep. Dominicana",
                "start_date": "2022-01",
                "end_date": "2023-05",
                "technologies": ["C#", ".NET", "PostgreSQL"],
                "bullets": [
                    "Engineered centralized POS and inventory control application.",
                    "Optimized invoice querying eliminating communication silos.",
                ],
            }
        ],
        certifications=[
            {"name": "Scrum Foundation Professional Certificate (SFPC)", "issuer": "CertiProf", "issue_date": "2023", "credential_url": "https://certiprof.com/verify"},
            {"name": "CCNA: Introduction to Networks", "issuer": "Cisco", "issue_date": "2022"},
        ],
        languages=[
            {"language": "Spanish", "proficiency": "Native"},
            {"language": "English", "proficiency": "B2 / Advanced"},
        ],
        projects=[
            {"name": "Personal Portfolio", "description": "Interactive showcase built with React.", "technologies": ["React", "TypeScript"], "url": "https://tonyml.com"}
        ],
        skills={
            "programming_ml": [{"language": "C#", "proficiency": "Advanced"}, {"language": "Java", "proficiency": "Intermediate"}],
            "domain_expertise": [".NET", "Spring Boot"],
            "software_tools": ["PostgreSQL", "SQL Server", "Docker", "Git"],
        },
    )
    # Attach relationship mock
    profile.user = user

    summary = _build_candidate_summary(profile)

    # 1. Contact & links
    assert "Antony Jafeth Monge López" in summary
    assert "antonyml2016@gmail.com" in summary
    assert "+506 8545-6150" in summary
    assert "https://linkedin.com/in/antony-monge-lopez" in summary
    assert "https://github.com/AntonyML" in summary
    assert "https://tonyml.com" in summary

    # 2. Both education items (no [:2] drop)
    assert "Universidad de Costa Rica" in summary
    assert "CTP Umberto Melloni" in summary

    # 3. Experience with context & technologies
    assert "Armería - Rep. Dominicana" in summary
    assert "Technologies: C#, .NET, PostgreSQL" in summary
    assert "Engineered centralized POS" in summary

    # 4. Certifications
    assert "Scrum Foundation Professional Certificate (SFPC)" in summary
    assert "CertiProf" in summary
    assert "CCNA: Introduction to Networks" in summary

    # 5. Languages
    assert "Spanish: Native" in summary
    assert "English: B2 / Advanced" in summary

    # 6. Projects & Skills
    assert "Personal Portfolio" in summary
    assert "C#" in summary
    assert "PostgreSQL" in summary


def test_cv_linter_detects_hallucinated_skills():
    """Verify CV Linter flags technologies not declared by the candidate."""
    profile = CandidateProfile(
        id="prof-123",
        user_id="user-123",
        skills={
            "programming_ml": [{"language": "C#"}, {"language": "Java"}],
            "domain_expertise": [".NET"],
            "software_tools": ["PostgreSQL", "Docker"],
        },
        experience=[
            {
                "company": "Tech Corp",
                "title": "Software Developer",
                "technologies": ["C#", "SQL Server"],
                "bullets": ["Developed backend services."],
            }
        ],
        certifications=[{"name": "AWS Certified Developer", "issuer": "Amazon"}],
    )

    # Generated CV contains hallucinated Kotlin and Python
    cv_output = {
        "cv": {
            "first_name": "Antony",
            "last_name": "Monge",
            "email": "antony@test.com",
            "skills": [
                {
                    "label": "Languages",
                    "skills": [{"name": "C#"}, {"name": "Kotlin"}, {"name": "Python"}],
                }
            ],
            "experience": [
                {
                    "company": "Tech Corp",
                    "title": "Software Developer",
                    "bullets": ["Developed robust enterprise backend services in C#."],
                }
            ],
            "certifications": [],
        }
    }

    issues = lint_cv(cv_output, profile)

    # Must flag Kotlin and Python as hallucinated
    assert any("Kotlin" in issue for issue in issues)
    assert any("Python" in issue for issue in issues)
    # Must flag missing certifications
    assert any("Certifications exist in the candidate profile" in issue for issue in issues)
