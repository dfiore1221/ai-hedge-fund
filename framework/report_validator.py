import json
import re
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_FRAMEWORK_PATH = PROJECT_ROOT / "framework" / "investment_framework.json"


def load_framework(path=DEFAULT_FRAMEWORK_PATH):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def validate_report(report_text, framework=None):
    framework = framework or load_framework()

    section_results = validate_required_sections(
        report_text,
        framework["required_sections"],
    )
    scorecard_results = validate_required_terms(
        report_text,
        framework["required_scorecard_items"],
    )
    evidence_results = validate_required_terms(
        report_text,
        framework["required_evidence_terms"],
    )
    final_rating_result = validate_final_rating(
        report_text,
        framework["allowed_final_ratings"],
    )
    prohibited_result = validate_prohibited_language(
        report_text,
        framework["prohibited_terms"],
    )

    checks = {
        "required_sections": section_results,
        "scorecard": scorecard_results,
        "evidence": evidence_results,
        "final_rating": final_rating_result,
        "prohibited_language": prohibited_result,
    }

    return {
        "framework_version": framework["version"],
        "quality_score": calculate_quality_score(checks, framework["quality_weights"]),
        "passed": all(check["passed"] for check in checks.values()),
        "checks": checks,
    }


def validate_required_sections(report_text, required_sections):
    missing = []
    for section in required_sections:
        if not section_exists(report_text, section):
            missing.append(section)

    return {
        "passed": not missing,
        "missing": missing,
        "present_count": len(required_sections) - len(missing),
        "required_count": len(required_sections),
    }


def validate_required_terms(report_text, required_terms):
    lower_text = report_text.lower()
    missing = [term for term in required_terms if term.lower() not in lower_text]

    return {
        "passed": not missing,
        "missing": missing,
        "present_count": len(required_terms) - len(missing),
        "required_count": len(required_terms),
    }


def validate_final_rating(report_text, allowed_ratings):
    rating = extract_final_rating(report_text)
    normalized = normalize_rating(rating)
    allowed = {normalize_rating(value) for value in allowed_ratings}

    return {
        "passed": normalized in allowed,
        "rating": rating,
        "allowed_ratings": allowed_ratings,
    }


def validate_prohibited_language(report_text, prohibited_terms):
    lower_text = report_text.lower()
    found = [term for term in prohibited_terms if term.lower() in lower_text]

    return {
        "passed": not found,
        "found": found,
    }


def calculate_quality_score(checks, weights):
    score = 0.0

    for check_name, weight in weights.items():
        check = checks[check_name]
        if "required_count" in check and check["required_count"]:
            score += weight * (check["present_count"] / check["required_count"])
        elif check["passed"]:
            score += weight

    return round(score, 1)


def section_exists(report_text, section):
    pattern = rf"(?:^|\n)\s*(?:#+\s*)?(?:\d+\.\s*)?{re.escape(section)}\b"
    return re.search(pattern, report_text, flags=re.IGNORECASE) is not None


def extract_final_rating(report_text):
    match = re.search(r"Final Rating:\s*([^\n]+)", report_text, flags=re.IGNORECASE)
    if not match:
        return None
    return match.group(1).strip().strip("*")


def normalize_rating(rating):
    if rating is None:
        return None
    return rating.strip().strip("*").lower()


def format_validation_report(validation):
    lines = [
        "# Research Framework Validation",
        "",
        f"Framework version: {validation['framework_version']}",
        f"Quality score: {validation['quality_score']}/100",
        f"Passed: {validation['passed']}",
        "",
        "## Checks",
    ]

    for check_name, check in validation["checks"].items():
        lines.append("")
        lines.append(f"### {check_name.replace('_', ' ').title()}")
        lines.append(f"Passed: {check['passed']}")

        if "missing" in check and check["missing"]:
            lines.append("Missing:")
            for item in check["missing"]:
                lines.append(f"- {item}")

        if "found" in check and check["found"]:
            lines.append("Found prohibited language:")
            for item in check["found"]:
                lines.append(f"- {item}")

        if "rating" in check:
            lines.append(f"Rating: {check['rating']}")

    return "\n".join(lines) + "\n"
