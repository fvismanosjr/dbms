#!/usr/bin/env python3
"""
SQL-DBMS Test Runner
Runs all feature tests and compares output against expected outputs.
"""

import subprocess
import shutil
import os
import re
import sys
import argparse
from dataclasses import dataclass
from typing import List


TEST_DIR = "test"
PYTHON = sys.executable

TEST_FILES = [
    "test_update",
    "test_index",
    "test_transaction",
    "test_integration",
]


@dataclass
class TestResult:
    name: str
    passed: bool
    output: str
    expected: str
    diff: str
    duration_ms: float


def strip_comments(sql: str) -> str:
    lines = sql.split('\n')
    cleaned = []
    for line in lines:
        line = line.strip()
        if line and not line.startswith('--'):
            cleaned.append(line)
    return '\n'.join(cleaned)


def normalize_output(output: str) -> str:
    """Normalize output for comparison by:
    1. Normalizing line endings
    2. Removing prompts
    3. Stripping trailing whitespace
    4. Removing empty lines
    """
    # Normalize line endings first
    output = output.replace('\r\n', '\n').replace('\r', '\n')
    lines = output.split('\n')
    cleaned = []
    for line in lines:
        # Remove prompt prefix
        line = re.sub(r'^(DB_\d{4}-\d+>\s*)+', '', line)
        line = line.rstrip()
        if line:
            cleaned.append(line)
    return '\n'.join(cleaned)


def sort_select_rows(output: str) -> str:
    """Sort data rows within SELECT result tables for order-independent comparison.

    The dbm module returns keys in insertion order on some platforms (dbm.dumb)
    and hash order on others (dbm.gnu). This causes SELECT * results to appear
    in different orders across platforms. We sort the data rows so comparison
    is order-independent.
    """
    lines = output.split('\n')
    result = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.startswith('+-'):
            # Found start of an ASCII table (SELECT result)
            table_parts = [line]  # top separator
            i += 1

            # Collect header row
            if i < len(lines) and lines[i].startswith('|'):
                table_parts.append(lines[i])
                i += 1

            # Check for middle separator (+- line after header)
            has_middle = False
            if i < len(lines) and lines[i].startswith('+-'):
                table_parts.append(lines[i])  # middle separator
                i += 1
                has_middle = True

            # Collect data rows
            data_rows = []
            while i < len(lines) and lines[i].startswith('|'):
                data_rows.append(lines[i])
                i += 1

            # Collect bottom separator
            if i < len(lines) and lines[i].startswith('+-'):
                table_parts.append(lines[i])
                i += 1

            # Sort data rows alphabetically and reconstruct table
            data_rows.sort()
            if has_middle:
                result.extend(table_parts[:3])   # top, header, middle
            else:
                result.extend(table_parts[:2])    # top, header
            result.extend(data_rows)
            if len(table_parts) > (3 if has_middle else 2):
                result.append(table_parts[-1])   # bottom separator
        else:
            result.append(line)
            i += 1

    return '\n'.join(result)


def run_single_test(test_name: str) -> TestResult:
    sql_file = os.path.join(TEST_DIR, f"{test_name}.sql")
    expected_file = os.path.join(TEST_DIR, f"{test_name}_expected.txt")

    # Verify files exist
    if not os.path.exists(sql_file):
        return TestResult(
            name=test_name,
            passed=False,
            output="",
            expected="",
            diff=f"SQL file not found: {sql_file}",
            duration_ms=0,
        )

    if not os.path.exists(expected_file):
        return TestResult(
            name=test_name,
            passed=False,
            output="",
            expected="",
            diff=f"Expected output file not found: {expected_file}",
            duration_ms=0,
        )

    # Read expected output
    with open(expected_file, "r", encoding="utf-8") as f:
        expected = f.read()

    # Clean DB directory
    if os.path.exists("DB"):
        shutil.rmtree("DB")

    # Read and clean SQL
    with open(sql_file, "r", encoding="utf-8") as f:
        sql = f.read()
    sql = strip_comments(sql)

    # Run test
    import time
    start = time.time()
    result = subprocess.run(
        [PYTHON, "run.py"],
        input=sql,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
    )
    duration_ms = (time.time() - start) * 1000

    output = result.stdout
    if result.stderr:
        output += "\n[STDERR]\n" + result.stderr

    # Compare with robust normalization (order-independent SELECT rows)
    norm_output = sort_select_rows(normalize_output(output))
    norm_expected = sort_select_rows(normalize_output(expected))

    if norm_output == norm_expected:
        return TestResult(
            name=test_name,
            passed=True,
            output=output,
            expected=expected,
            diff="",
            duration_ms=duration_ms,
        )
    else:
        # Generate diff
        diff_lines = []
        out_lines = norm_output.split('\n')
        exp_lines = norm_expected.split('\n')
        max_lines = max(len(out_lines), len(exp_lines))
        for i in range(max_lines):
            o = out_lines[i] if i < len(out_lines) else "<missing>"
            e = exp_lines[i] if i < len(exp_lines) else "<missing>"
            if o != e:
                diff_lines.append(f"Line {i+1}:")
                diff_lines.append(f"  EXPECTED: {e}")
                diff_lines.append(f"  ACTUAL:   {o}")
        diff = '\n'.join(diff_lines[:50])  # Limit diff size
        
        # Save actual output for CI debugging
        actual_file = os.path.join(TEST_DIR, f"{test_name}_actual.txt")
        with open(actual_file, "w", encoding="utf-8") as f:
            f.write(output)
        
        return TestResult(
            name=test_name,
            passed=False,
            output=output,
            expected=expected,
            diff=diff,
            duration_ms=duration_ms,
        )


def run_all_tests(verbose: bool = False) -> List[TestResult]:
    print(f"Running {len(TEST_FILES)} test suite(s)...\n")

    results = []
    for name in TEST_FILES:
        result = run_single_test(name)
        results.append(result)
        if verbose or not result.passed:
            print_result(result)

    return results


def print_result(result: TestResult):
    status = "PASS" if result.passed else "FAIL"
    icon = "[OK]" if result.passed else "[XX]"
    print(f"{icon} {status}: {result.name} ({result.duration_ms:.0f}ms)")
    if not result.passed and result.diff:
        print("   Diff (first 10 lines):")
        for line in result.diff.split('\n')[:10]:
            print(f"     {line}")
    print()


def generate_all_expected():
    """Regenerate all expected output files from current test runs."""
    print("Regenerating expected outputs...")
    for name in TEST_FILES:
        result = run_single_test(name)
        out_file = os.path.join(TEST_DIR, f"{name}_expected.txt")

        # Normalize before writing so files are platform-independent
        normalized = sort_select_rows(normalize_output(result.output))

        with open(out_file, "w", encoding="utf-8") as f:
            f.write(normalized)
        print(f"  Written: {out_file}")
    print("Done!")


def main():
    parser = argparse.ArgumentParser(description="SQL-DBMS Test Runner")
    parser.add_argument("--verbose", "-v", action="store_true", help="Show all test output")
    parser.add_argument("--test", "-t", type=str, help="Run a specific test (e.g., test_update)")
    parser.add_argument("--generate", "-g", action="store_true", help="Regenerate expected outputs")
    args = parser.parse_args()

    if args.generate:
        generate_all_expected()
        return

    if args.test:
        if args.test not in TEST_FILES:
            print(f"Unknown test: {args.test}")
            print(f"Available: {', '.join(TEST_FILES)}")
            sys.exit(1)
        results = [run_single_test(args.test)]
    else:
        results = run_all_tests(verbose=args.verbose)

    # Summary
    passed = sum(1 for r in results if r.passed)
    total = len(results)
    print("=" * 50)
    print(f"Results: {passed}/{total} passed")
    print("=" * 50)

    if passed < total:
        sys.exit(1)


if __name__ == "__main__":
    main()
