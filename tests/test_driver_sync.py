"""Checks that the two lab drivers run the same code.

drivers/MM4250_QCodes_driver_commented.py is the tutorial copy of
drivers/MM4250_QCodes_driver.py. Comments and docstrings may differ; the
code, including error message strings, may not. Parses both files without
importing them, so no hidapi or hardware is needed. Run from the repo root:

    python -m pytest tests/test_driver_sync.py
"""

import ast
import difflib
from pathlib import Path

DRIVERS = Path(__file__).resolve().parents[1] / "drivers"
PLAIN = DRIVERS / "MM4250_QCodes_driver.py"
COMMENTED = DRIVERS / "MM4250_QCodes_driver_commented.py"


def _code_lines(path):
    """Source with comments, docstrings and formatting normalized away."""
    tree = ast.parse(path.read_text())
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if (
            isinstance(body, list)
            and body
            and isinstance(body[0], ast.Expr)
            and isinstance(body[0].value, ast.Constant)
            and isinstance(body[0].value.value, str)
        ):
            node.body = body[1:] or [ast.Pass()]
    return ast.unparse(tree).splitlines()


def test_commented_driver_matches_plain_driver():
    plain = _code_lines(PLAIN)
    commented = _code_lines(COMMENTED)
    diff = "\n".join(
        difflib.unified_diff(
            plain, commented, PLAIN.name, COMMENTED.name, lineterm="", n=1
        )
    )
    assert plain == commented, (
        "The two drivers' code differs (comments and docstrings ignored). "
        "Make the same change in both files:\n" + diff
    )
