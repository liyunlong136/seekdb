#!/usr/bin/env python3
"""Split the cost of ``tokenize()`` into the fixed overhead and the work.

The fulltext benchmark reports one number per metric, which does not tell
whether a tokenize call is dominated by the per call setup (parser
construction, parser property parsing, dictionary lookup) or by the actual
segmentation of the text.  This helper measures the same statement with
texts of growing size so that the two parts can be separated:

* the empty text costs the fixed overhead only,
* the slope of the curve is the per character cost.

Usage::

    python3 tools/local_ci/profile_tokenize.py --rounds 300
"""
from __future__ import annotations

import argparse
import statistics
import sys
import time

IK_TEXT = (
    "OceanBase是一款非常稳定的数据库，全文索引的分词器热路径优化包括解析器实例复用、"
    "停用词全局单例、内存池化数据结构等技术，在大量文档索引和查询场景下可以显著降低 CPU 开销。"
)
BENG_TEXT = (
    "The quick brown fox jumps over the lazy dog. Full-text search indexing "
    "requires efficient tokenization on the hot path."
)


def measure(cursor, sql, rounds, warmup):
    for _ in range(warmup):
        cursor.execute(sql)
        cursor.fetchall()
    samples = []
    for _ in range(5):
        started = time.monotonic()
        for _ in range(rounds):
            cursor.execute(sql)
            cursor.fetchall()
        samples.append((time.monotonic() - started) * 1000.0 / rounds)
    return min(samples), statistics.median(samples)


def quote(text):
    return "'" + text.replace("\\", "\\\\").replace("'", "''") + "'"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=2881)
    parser.add_argument("--user", default="root")
    parser.add_argument("--password", default="")
    parser.add_argument("--rounds", type=int, default=300)
    parser.add_argument("--warmup", type=int, default=30)
    args = parser.parse_args(argv)

    import pymysql

    connection = pymysql.connect(
        host=args.host,
        port=args.port,
        user=args.user,
        password=args.password,
        charset="utf8mb4",
        autocommit=True,
    )

    cases = [
        ("select 1", "SELECT 1", 0),
        ("tokenize empty ik", "SELECT tokenize('', 'ik')", 0),
        ("tokenize 'a' ik", "SELECT tokenize('a', 'ik')", 1),
        ("tokenize 'OceanBase' ik", "SELECT tokenize('OceanBase', 'ik')", 8),
        ("tokenize ik text", "SELECT tokenize(%s, 'ik')" % quote(IK_TEXT), len(IK_TEXT)),
        ("tokenize ik text x4", "SELECT tokenize(%s, 'ik')" % quote(IK_TEXT * 4), len(IK_TEXT) * 4),
        ("tokenize ik text x16", "SELECT tokenize(%s, 'ik')" % quote(IK_TEXT * 16), len(IK_TEXT) * 16),
        ("tokenize beng text", "SELECT tokenize(%s, 'beng')" % quote(BENG_TEXT), len(BENG_TEXT)),
        ("tokenize beng text x16", "SELECT tokenize(%s, 'beng')" % quote(BENG_TEXT * 16), len(BENG_TEXT) * 16),
    ]

    print("%-28s %10s %10s %8s" % ("case", "min_ms", "median_ms", "chars"))
    with connection.cursor() as cursor:
        for name, sql, length in cases:
            best, median = measure(cursor, sql, args.rounds, args.warmup)
            print("%-28s %10.4f %10.4f %8d" % (name, best, median, length))
    connection.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
