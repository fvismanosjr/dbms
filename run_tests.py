#!/usr/bin/env python3
"""
SQL-DBMS Test Runner
Runs all feature tests and compares output against expected outputs.
Supports parallel execution via multiprocessing.
"""

import subprocess
import shutil
import os
import re
import sys
import argparse
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass
from typing import List, Optional


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
    """Normalize output for comparison by removing prompts and extra whitespace."""
    lines = output.split('\n')
    cleaned = []
    for line in lines:
        # Remove prompt prefix
        line = re.sub(r'^DB_\d{4}-\d+>\s*', '', line)
        line = line.rstrip()
        if line:
            cleaned.append(line)
    return '\n'.join(cleaned)


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

    # Compare
    norm_output = normalize_output(output)
    norm_expected = normalize_output(expected)

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
        return TestResult(
            name=test_name,
            passed=False,
            output=output,
            expected=expected,
            diff=diff,
            duration_ms=duration_ms,
        )


def run_all_tests(parallel: bool = False, verbose: bool = False) -> List[TestResult]:
    print(f"Running {len(TEST_FILES)} test suite(s)...")
    print(f"Parallel: {'yes' if parallel else 'no'}\n")

    if parallel:
        print("WARNING: Local parallel mode disabled due to shared DB state.")
        print("Use the CI workflow for true parallel execution.\n")
        results = []
        for name in TEST_FILES:
            result = run_single_test(name)
            results.append(result)
            if verbose or not result.passed:
                print_result(result)
    else:
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


def main():
    parser = argparse.ArgumentParser(description="SQL-DBMS Test Runner")
    parser.add_argument("--parallel", "-p", action="store_true", help="Run tests in parallel")
    parser.add_argument("--verbose", "-v", action="store_true", help="Show all test output")
    parser.add_argument("--test", "-t", type=str, help="Run a specific test (e.g., test_update)")
    parser.add_argument("--generate", "-g", action="store_true", help="Regenerate expected outputs")
    args = parser.parse_args()

    if args.generate:
        print("Regenerating expected outputs...")
        for name in TEST_FILES:
            result = run_single_test(name)
            out_file = os.path.join(TEST_DIR, f"{name}_expected.txt")
            with open(out_file, "w", encoding="utf-8") as f:
                f.write(result.output)
            print(f"  Written: {out_file}")
        print("Done!")
        return

    if args.test:
        if args.test not in TEST_FILES:
            print(f"Unknown test: {args.test}")
            print(f"Available: {', '.join(TEST_FILES)}")
            sys.exit(1)
        results = [run_single_test(args.test)]
    else:
        results = run_all_tests(parallel=args.parallel, verbose=args.verbose)

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
