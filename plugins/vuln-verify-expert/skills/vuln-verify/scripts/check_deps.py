#!/usr/bin/env python
"""Read-only preflight for the vuln-verify skill.

The platform owns dependency installation and MCP configuration. This script only
reports what is available in the current Worker environment.
"""

import importlib.util
import os
import sys


def check_python_package(label: str, import_name: str) -> bool:
    present = importlib.util.find_spec(import_name) is not None
    print(f"  [{'OK' if present else 'MISSING'}] {label}")
    return present


def main() -> int:
    print("Checking dependencies for vuln-verify skill...\n")
    print("Python packages:")
    docx_ok = check_python_package("python-docx", "docx")
    playwright_ok = check_python_package("Playwright Python runtime", "playwright")

    print("\nPlatform capabilities:")
    browser_capability = os.getenv("AUTOVERIFY_BROWSER_CAPABILITY", "").strip()
    if browser_capability:
        print(f"  [OK] browser capability: {browser_capability}")
        browser_ok = True
    else:
        print("  [MISSING] browser capability (Playwright MCP or Chrome DevTools MCP)")
        browser_ok = False
    print("  [OK] Python 3.x")

    print("\nNo packages or MCP configuration are modified by this script.")
    required = docx_ok and (playwright_ok or browser_ok)
    if required:
        print("Preflight passed.")
        return 0
    print("Preflight failed: the Worker image or platform capability contract is incomplete.")
    return 1


if __name__ == "__main__":
    sys.exit(main())