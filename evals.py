
import argparse
import csv
import json

from datetime import datetime
from pathlib import Path

from main import (
    triage_issue_data,
    fetch_product_area_labels,
)


# --------------------------------------------------
# Configuration
# --------------------------------------------------

CASES_FILE = Path("eval_cases.json")
REPORTS_FOLDER = Path("eval_reports")

VALID_DECISIONS = {
    "STAY",
    "TRANSFER",
    "REVIEW",
    "NEEDS_MORE_INFORMATION",
}


# --------------------------------------------------
# Load cases
# --------------------------------------------------

def load_cases(suite: str) -> list[dict]:
    with CASES_FILE.open(
        "r",
        encoding="utf-8",
    ) as file:
        data = json.load(file)

    regression = data.get("regression", [])
    fresh = data.get("fresh", [])

    if not isinstance(regression, list):
        raise ValueError(
            "'regression' must be a list."
        )

    if not isinstance(fresh, list):
        raise ValueError(
            "'fresh' must be a list."
        )

    if suite == "regression":
        cases = regression
    elif suite == "fresh":
        cases = fresh
    else:
        cases = regression + fresh

    required_fields = {
        "name",
        "url",
        "expected_decision",
        "expected_repository",
        "expected_product_area",
    }

    for index, case in enumerate(cases, start=1):
        missing = required_fields - set(case)

        if missing:
            raise ValueError(
                f"Case #{index} missing fields: "
                f"{sorted(missing)}"
            )

        if case["expected_decision"] not in VALID_DECISIONS:
            raise ValueError(
                f"Invalid decision in case #{index}"
            )

    return cases


# --------------------------------------------------
# Evaluate one case
# --------------------------------------------------

def evaluate_case(test: dict) -> dict:
    print(f"\nTesting: {test['name']}")

    row = {
        "name": test["name"],
        "issue_url": test["url"],
        "expected_decision": test["expected_decision"],
        "actual_decision": "",
        "expected_repository": (
            test["expected_repository"] or ""
        ),
        "actual_repository": "",
        "expected_product_area": (
            test["expected_product_area"] or ""
        ),
        "actual_product_area": "",
        "decision_correct": False,
        "repository_correct": False,
        "product_area_correct": False,
        "passed": False,
        "status": "",
        "reason": "",
        "error": "",
    }

    try:
        result = triage_issue_data(test["url"])

        row["actual_decision"] = result.decision
        row["actual_repository"] = (
            result.suggested_repository or ""
        )
        row["actual_product_area"] = (
            result.product_area or ""
        )
        row["reason"] = result.reason

        row["decision_correct"] = (
            result.decision
            == test["expected_decision"]
        )

        row["repository_correct"] = (
            result.suggested_repository
            == test["expected_repository"]
        )

        expected_area = test["expected_product_area"]

        if expected_area == "ANY_VALID":
            valid_labels = fetch_product_area_labels()

            row["product_area_correct"] = (
                result.product_area in valid_labels
            )
        else:
            row["product_area_correct"] = (
                result.product_area == expected_area
            )

        row["passed"] = all([
            row["decision_correct"],
            row["repository_correct"],
            row["product_area_correct"],
        ])

        row["status"] = (
            "PASS" if row["passed"] else "FAIL"
        )

        print("Result:", row["status"])

        print(
            "Decision:",
            test["expected_decision"],
            "->",
            result.decision,
        )

        print(
            "Repository:",
            test["expected_repository"],
            "->",
            result.suggested_repository,
        )

        print(
            "Product Area:",
            expected_area,
            "->",
            result.product_area,
        )

    except Exception as error:
        row["status"] = "ERROR"
        row["error"] = str(error)

        print("ERROR:", error)

    return row


# --------------------------------------------------
# Save CSV report
# --------------------------------------------------

def save_report(
    rows: list[dict],
    suite: str,
) -> Path:
    REPORTS_FOLDER.mkdir(
        parents=True,
        exist_ok=True,
    )

    timestamp = datetime.now().strftime(
        "%Y%m%d_%H%M%S_%f"
    )

    path = (
        REPORTS_FOLDER
        / f"eval_{suite}_{timestamp}.csv"
    )

    fieldnames = [
        "name",
        "issue_url",
        "expected_decision",
        "actual_decision",
        "expected_repository",
        "actual_repository",
        "expected_product_area",
        "actual_product_area",
        "decision_correct",
        "repository_correct",
        "product_area_correct",
        "passed",
        "status",
        "reason",
        "error",
    ]

    with path.open(
        "w",
        newline="",
        encoding="utf-8",
    ) as file:
        writer = csv.DictWriter(
            file,
            fieldnames=fieldnames,
        )

        writer.writeheader()
        writer.writerows(rows)

    return path


# --------------------------------------------------
# Main
# --------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Evaluate the Sentry triage agent."
    )

    parser.add_argument(
        "--suite",
        choices=[
            "regression",
            "fresh",
            "all",
        ],
        default="regression",
    )

    args = parser.parse_args()

    cases = load_cases(args.suite)

    if not cases:
        print(
            f"No evaluation cases in '{args.suite}'."
        )
        return

    print("Sentry Triage Evaluation")
    print("------------------------")
    print("Suite:", args.suite)
    print("Cases:", len(cases))

    results = []

    for test in cases:
        results.append(evaluate_case(test))

    passed = sum(
        row["status"] == "PASS"
        for row in results
    )

    failed = sum(
        row["status"] == "FAIL"
        for row in results
    )

    errors = sum(
        row["status"] == "ERROR"
        for row in results
    )

    print("\n-------------------------")
    print("Evaluation results")
    print("-------------------------")
    print("Passed:", passed)
    print("Failed:", failed)
    print("Errors:", errors)
    print("Total:", len(results))

    if results:
        print(
            f"Pass rate: {passed / len(results) * 100:.1f}%"
        )

    path = save_report(results, args.suite)

    print("\nCSV report saved to:", path)


if __name__ == "__main__":
    main()
