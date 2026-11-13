#!/usr/bin/env python3
"""A small mysqltest-compatible driver for the seekdb competition test suites.

The upstream Perl ``mysqltest`` driver is not part of the seekdb repository, so
this script re-implements the subset of the ``.test`` / ``.result`` dialect the
competition suites use:

* ``--echo``, ``--error``, ``--disable_result_log`` / ``--enable_result_log``
* ``--disable_warnings`` / ``--enable_warnings``
* ``--source <file>`` (relative to the mysql_test root)
* ``let $VAR = ...`` and ``eval ...``
* plain SQL statements, possibly spanning several lines

Statements are sent to a running observer over the MySQL protocol with
``pymysql``.  The produced transcript is compared against the ``.result`` file
that ships with the suite, using the same "ignore trailing horizontal
whitespace" rule as the upstream runner.
"""
from __future__ import annotations

import argparse
import difflib
import os
import re
import sys
import time
import traceback
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
MYSQL_TEST_ROOT = REPO_ROOT / "tools" / "deploy" / "mysql_test"
SUITE_ROOT = MYSQL_TEST_ROOT / "test_suite"
OUT_ROOT = Path(os.environ.get("SEEKDB_LOCAL_CI_OUT", "/tmp/seekdb_local_ci"))


class CaseError(RuntimeError):
    pass


# --------------------------------------------------------------------------
# .test parsing
# --------------------------------------------------------------------------
class Statement:
    def __init__(self, text, line_no, expect_errors=None, result_log=True, echo=None):
        self.text = text
        self.line_no = line_no
        self.expect_errors = expect_errors
        self.result_log = result_log
        self.echo = echo


def expand(text, variables):
    def repl(match):
        name = match.group(1)
        if name not in variables:
            raise CaseError("undefined variable $%s" % name)
        return variables[name]

    return re.sub(r"\$(\w+)", repl, text)


def _resolve_source(target):
    """Locate a sourced include file.

    The suites refer to the include files either relative to the mysql_test
    root (``include/foo.inc``) or relative to the directory that holds it
    (``mysql_test/include/foo.inc``).
    """
    root = MYSQL_TEST_ROOT.resolve()
    candidates = [
        MYSQL_TEST_ROOT / target,
        MYSQL_TEST_ROOT.parent / target,
        MYSQL_TEST_ROOT / "include" / Path(target).name,
    ]
    for candidate in candidates:
        try:
            resolved = candidate.resolve()
        except OSError:
            continue
        if root not in resolved.parents and resolved != root:
            continue
        if resolved.is_file():
            return resolved
    return None


def parse_test(path, variables, source_stack=None):
    """Return the list of statements and the echo lines for one .test file."""
    actions = []
    expect_errors = None
    result_log = True
    pending = []
    pending_start = 0
    lines = path.read_text(encoding="utf-8").splitlines()
    index = 0
    while index < len(lines):
        raw = lines[index]
        stripped = raw.strip()
        line_no = index + 1
        index += 1

        if not stripped:
            continue
        if stripped.startswith("#"):
            continue
        if stripped.startswith("--"):
            directive = stripped[2:].strip()
            if directive.startswith("echo "):
                actions.append(("echo", directive[5:]))
            elif directive.startswith("error "):
                codes = []
                for item in directive[6:].split(","):
                    item = item.strip()
                    if not item:
                        continue
                    try:
                        codes.append(int(item))
                    except ValueError:
                        pass
                expect_errors = codes or [0]
            elif directive in ("disable_result_log", "enable_result_log"):
                result_log = directive == "enable_result_log"
            elif directive in ("disable_warnings", "enable_warnings"):
                pass
            elif directive.startswith("source "):
                target = directive[7:].strip()
                source_path = _resolve_source(target)
                if source_path is None:
                    raise CaseError("missing source file: %s" % target)
                sub = parse_test(source_path, variables)
                actions.extend(sub)
            elif directive.startswith("let ") or directive.startswith("Let "):
                body = directive[4:].strip()
                if "=" not in body:
                    raise CaseError("unsupported let directive: %s" % directive)
                name, value = body.split("=", 1)
                variables[name.strip().lstrip("$")] = value.strip().strip("'\"")
            elif directive.startswith("replace_column") or directive.startswith("sorted_result"):
                raise CaseError("unsupported directive: %s" % directive)
            else:
                # Unknown directives are ignored on purpose: they only affect
                # formatting of the reference transcript.
                pass
            continue

        if stripped.lower().startswith("connection "):
            continue
        lowered = stripped.lower()
        if lowered.startswith("connect ") or lowered.startswith("connect("):
            # A connection switch, not a statement: the reference transcript
            # does not contain it either.
            continue
        if lowered.startswith("disconnect "):
            continue
        if stripped.lower().startswith("let "):
            body = stripped[4:].strip()
            if "=" in body:
                name, value = body.split("=", 1)
                variables[name.strip().lstrip("$")] = value.strip().strip("'\"")
            continue

        if not pending:
            pending_start = line_no
        # mysqltest echoes a multi line statement without the leading blanks of
        # its continuation lines, the reference transcripts are written that way.
        pending.append(stripped)
        joined = "\n".join(pending)
        if not joined.rstrip().endswith(";"):
            continue

        sql = joined.rstrip()
        pending = []
        if stripped.lower().startswith("eval "):
            sql = expand(sql[5:].strip(), variables)
        actions.append(
            (
                "sql",
                Statement(
                    sql,
                    pending_start,
                    expect_errors=expect_errors,
                    result_log=result_log,
                ),
            )
        )
        expect_errors = None
    if pending:
        raise CaseError("unterminated statement near line %d" % pending_start)
    return actions


# --------------------------------------------------------------------------
# result formatting
# --------------------------------------------------------------------------
def format_value(value):
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, bytes):
        return value.decode("utf-8", "replace")
    return str(value)


def render(statement, columns, rows, error=None):
    out = []
    if statement.result_log:
        # the reference transcript echoes every line of a statement as its own
        # line, so a multi line statement must not be one transcript element
        out.extend(statement.text.splitlines())
        if error is None:
            if columns:
                out.append("\t".join(str(c) for c in columns))
                for row in rows:
                    out.append("\t".join(format_value(v) for v in row))
        else:
            out.append(format_error(error))
    return out


_SQLSTATE_CACHE = None


def _mysql_errno_symbols():
    """ER_* symbol -> number, from the checked in mysql_errno.h."""
    symbols = {}
    path = REPO_ROOT / "src" / "share" / "mysql_errno.h"
    if path.is_file():
        pattern = re.compile(r"^#define\s+(ER_\w+)\s+(\d+)\s*$")
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            match = pattern.match(line.strip())
            if match is not None:
                symbols[match.group(1)] = int(match.group(2))
    return symbols


def _sqlstate_map():
    """errno -> sqlstate, taken from the checked in error definitions.

    The server does not put the sqlstate into the error packet, the reference
    runner reads it from the same table, so it is parsed here once.  An error
    may name its native code either by number or by its ``ER_*`` symbol.
    """
    global _SQLSTATE_CACHE
    if _SQLSTATE_CACHE is not None:
        return _SQLSTATE_CACHE
    symbols = _mysql_errno_symbols()
    mapping = {}
    path = REPO_ROOT / "src" / "share" / "ob_errno.def"
    if path.is_file():
        pattern = re.compile(
            r"DEFINE_ERROR(?:_EXT|_EXT_DEP)?\s*\(\s*\w+\s*,\s*(-?\d+)\s*,"
            r"\s*(-?\d+|[A-Z_][A-Z0-9_]*)\s*,\s*\"([^\"]*)\"")
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            match = pattern.search(line)
            if match is None:
                continue
            internal = int(match.group(1))
            raw_native = match.group(2)
            sqlstate = match.group(3)
            if not sqlstate:
                continue
            native = int(raw_native) if raw_native.lstrip("-").isdigit() else symbols.get(raw_native, -1)
            if native > 0:
                mapping.setdefault(native, sqlstate)
            mapping.setdefault(abs(internal), sqlstate)
    _SQLSTATE_CACHE = mapping
    return mapping


def format_error(error):
    """Render an error the way the reference mysqltest transcript does."""
    args = getattr(error, "args", ())
    errno = args[0] if args and isinstance(args[0], int) else 0
    message = args[1] if len(args) > 1 and isinstance(args[1], str) else str(error)
    sqlstate = getattr(error, "sqlstate", None)
    if not sqlstate or sqlstate == "HY000":
        sqlstate = _sqlstate_map().get(errno, sqlstate or "HY000")
    return "ERROR %s: %s" % (sqlstate, message)


# --------------------------------------------------------------------------
# execution
# --------------------------------------------------------------------------
def connect(host, port, user, password, database):
    import pymysql

    return pymysql.connect(
        host=host,
        port=port,
        user=user,
        password=password,
        database=database,
        charset="utf8mb4",
        autocommit=True,
        cursorclass=pymysql.cursors.Cursor,
    )


def run_case(case_id, test_path, result_path, host, port, user, password, database):
    transcript = []
    variables = {}
    actions = parse_test(test_path, variables)
    connection = connect(host, port, user, password, database)
    try:
        with connection.cursor() as cursor:
            for kind, payload in actions:
                if kind == "echo":
                    transcript.append(payload)
                    continue
                statement = payload
                columns = []
                rows = []
                error = None
                try:
                    cursor.execute(statement.text)
                    if cursor.description:
                        columns = [d[0] for d in cursor.description]
                        rows = list(cursor.fetchall())
                except Exception as exc:  # noqa: BLE001
                    error = exc
                if statement.expect_errors is not None:
                    if error is None:
                        if 0 not in statement.expect_errors:
                            raise CaseError(
                                "line %d: expected error %s but the statement succeeded"
                                % (statement.line_no, statement.expect_errors)
                            )
                    else:
                        code = getattr(error, "args", [None])[0]
                        if not isinstance(code, int):
                            code = 0
                        if code not in statement.expect_errors:
                            raise CaseError(
                                "line %d: expected error %s, got %s (%s)"
                                % (statement.line_no, statement.expect_errors, code, error)
                            )
                elif error is not None:
                    raise CaseError(
                        "line %d: unexpected error: %s"
                        % (statement.line_no, error)
                    )
                transcript.extend(render(statement, columns, rows, error))
    except Exception as exc:  # noqa: BLE001
        # Keep what has been produced so far, it is the only way to see the
        # statements that ran before the failure.
        exc.transcript = list(transcript)
        raise
    finally:
        connection.close()
    return transcript


def normalize(lines):
    # The reference transcript keeps the indentation of the echoed statement
    # of a multi line query in a slightly different way than the reader does,
    # so the leading blanks are not significant for the comparison.
    return [line.strip() for line in lines]


def compare(expected_path, actual_lines):
    expected = expected_path.read_text(encoding="utf-8").splitlines()
    expected = [line.strip() for line in expected]
    actual = normalize(actual_lines)
    if expected == actual:
        return None
    diff = difflib.unified_diff(
        expected, actual, fromfile="expected", tofile="actual", lineterm=""
    )
    return "\n".join(diff)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", default="ai_function")
    parser.add_argument("--case", action="append", help="run only this case id")
    parser.add_argument("--host", default=os.environ.get("SEEKDB_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("SEEKDB_PORT", "2881")))
    parser.add_argument("--user", default=os.environ.get("SEEKDB_USER", "root"))
    parser.add_argument("--password", default=os.environ.get("SEEKDB_PASSWORD", ""))
    parser.add_argument("--database", default=os.environ.get("SEEKDB_DATABASE", "test"),
                        help="default database of the session, the reference runner uses 'test'")
    args = parser.parse_args(argv)

    suite_dir = SUITE_ROOT / args.suite
    test_dir = suite_dir / "t"
    result_dir = suite_dir / "r" / "mysql"
    if not test_dir.is_dir():
        raise SystemExit("unknown suite: %s" % args.suite)

    OUT_ROOT.mkdir(parents=True, exist_ok=True)
    results = []
    failures = []
    for test_path in sorted(test_dir.glob("*.test")):
        case_id = test_path.stem
        if args.case and case_id not in args.case:
            continue
        result_path = result_dir / (case_id + ".result")
        if not result_path.is_file():
            results.append((case_id, "SKIP", "no result file"))
            print("%s: SKIP (no result file)" % case_id, flush=True)
            continue
        started = time.monotonic()
        try:
            transcript = run_case(
                case_id,
                test_path,
                result_path,
                args.host,
                args.port,
                args.user,
                args.password,
                args.database,
            )
        except Exception as exc:  # noqa: BLE001
            detail = "".join(
                traceback.format_exception(type(exc), exc, exc.__traceback__)
            )
            (OUT_ROOT / (case_id + ".error.log")).write_text(detail, encoding="utf-8")
            partial = getattr(exc, "transcript", None)
            if partial:
                (OUT_ROOT / (case_id + ".actual.txt")).write_text(
                    "\n".join(partial) + "\n", encoding="utf-8")
            results.append((case_id, "FAIL", str(exc)))
            failures.append(case_id)
            print("%s: FAIL %s" % (case_id, exc), flush=True)
            continue
        diff = compare(result_path, transcript)
        elapsed = time.monotonic() - started
        if diff is None:
            results.append((case_id, "PASS", ""))
            print("%s: PASS (%.1fs)" % (case_id, elapsed), flush=True)
        else:
            (OUT_ROOT / (case_id + ".diff.txt")).write_text(diff, encoding="utf-8")
            (OUT_ROOT / (case_id + ".actual.txt")).write_text(
                "\n".join(transcript) + "\n", encoding="utf-8"
            )
            results.append((case_id, "FAIL", "result mismatch"))
            failures.append(case_id)
            print("%s: FAIL result mismatch" % case_id, flush=True)

    passed = not failures
    summary = "\n".join("%s %s %s" % item for item in results)
    (OUT_ROOT / "summary.txt").write_text(summary + "\n", encoding="utf-8")
    print("\n%s" % summary)
    print("overall: %s" % ("PASS" if passed else "FAIL"))
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
