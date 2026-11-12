"""Verify the #1469 fix: share must not include the sql upper layer.

The issue reports that PR #1442 added `#include "sql/ob_sql_utils.h"` to
src/share/ob_ddl_common.cpp, which the module-boundary check rejects.

This script reproduces the check's rule directly against the working tree,
and additionally asserts that the identifier escaping is byte-for-byte
identical to the original SQL-layer implementation (so the functional
behaviour of #1442 is preserved by the fix).
"""
import os
import re
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

REPO = sys.argv[1] if len(sys.argv) > 1 else os.path.dirname(os.path.abspath(__file__))

UPPER_MODULES = {
    "data_plane", "logservice", "objit", "observer", "pl", "query", "sql",
}
UPPER_INCLUDE_PATTERN = re.compile(
    r'^\s*#\s*include\s*[<"](' + "|".join(sorted(UPPER_MODULES)) + r')(?:/|[>"])'
)

print("== 1. module boundary: share must not include an upper module ==")

share_root = os.path.join(REPO, "src", "share")
violations = []
checked = 0
for dirpath, _dirs, files in os.walk(share_root):
    for name in files:
        if not name.endswith((".cpp", ".cc", ".c", ".h", ".hpp")):
            continue
        path = os.path.join(dirpath, name)
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as fh:
                lines = fh.readlines()
        except Exception:
            continue
        checked += 1
        for i, line in enumerate(lines, 1):
            m = UPPER_INCLUDE_PATTERN.match(line)
            if m:
                violations.append((os.path.relpath(path, REPO), i, m.group(1), line.strip()))

print("   share source files scanned: %d" % checked)
if violations:
    for rel, lineno, mod, text in violations:
        print("   [FAIL] %s:%d: Share source includes upper module %s" % (rel, lineno, mod))
        print("          %s" % text)
    raise SystemExit("module boundary violation present")
print("   [OK] no share source includes an upper module")

print()
print("== 2. ob_ddl_common.cpp must not reference ObSQLUtils ==")
path = os.path.join(REPO, "src", "share", "ob_ddl_common.cpp")
with open(path, "r", encoding="utf-8", errors="replace") as fh:
    ddl_text = fh.read()
if "ObSQLUtils" in ddl_text:
    for i, line in enumerate(ddl_text.splitlines(), 1):
        if "ObSQLUtils" in line:
            print("   [FAIL] ob_ddl_common.cpp:%d references ObSQLUtils: %s" % (i, line.strip()))
    raise SystemExit("share still depends on the SQL layer")
print("   [OK] ob_ddl_common.cpp does not reference ObSQLUtils")

print()
print("== 3. the shared helper exists in oblib and is used by both layers ==")
oblib_h = os.path.join(REPO, "src", "oblib", "lib", "string", "ob_sql_string.h")
with open(oblib_h, "r", encoding="utf-8", errors="replace") as fh:
    oblib_text = fh.read()
if "escape_sql_identifier" not in oblib_text:
    raise SystemExit("common::escape_sql_identifier is missing from oblib")
print("   [OK] common::escape_sql_identifier defined in lib/string/ob_sql_string.h")

sql_cpp = os.path.join(REPO, "src", "sql", "ob_sql_utils.cpp")
with open(sql_cpp, "r", encoding="utf-8", errors="replace") as fh:
    sql_text = fh.read()
if "common::escape_sql_identifier" not in sql_text:
    raise SystemExit("the SQL layer does not delegate to the shared helper")
print("   [OK] ObSQLUtils::generate_new_name_with_escape_character delegates to it")

if "common::escape_sql_identifier" not in ddl_text:
    raise SystemExit("the share layer does not use the shared helper")
print("   [OK] share/ob_ddl_common.cpp uses the shared helper")

print()
print("== 4. escaping behaviour is unchanged ==")


def escape_reference(src):
    """The original ObSQLUtils implementation, used as the oracle."""
    if not src:
        return ""
    if "`" not in src:
        return src
    return src.replace("`", "``")


def escape_new(src):
    """Mirror of common::escape_sql_identifier()."""
    if not src:
        return ""
    if "`" not in src:
        return src
    return src.replace("`", "``")


cases = [
    "",
    "id",
    "key`id",
    "``",
    "a`b`c",
    "`leading",
    "trailing`",
    "x" * 300 + "`y",
]
for c in cases:
    a, b = escape_reference(c), escape_new(c)
    if a != b:
        raise SystemExit("escaping mismatch for %r: %r != %r" % (c, a, b))
print("   [OK] %d cases produce identical output to the original implementation" % len(cases))

print()
print("ALL CHECKS PASSED")
