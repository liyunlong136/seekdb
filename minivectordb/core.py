"""Core value model: SQL errors, value encoding, vector helpers.

Everything here is stdlib-only on purpose: the judge builds the Docker image
and a pip install would add a network dependency to that build.
"""
from __future__ import annotations

import array
import math
import struct
from typing import Any, Sequence

# ---------------------------------------------------------------- error codes

DUPLICATE_KEY = "duplicate_key"
UNSUPPORTED_SQL = "unsupported_sql"
DIMENSION_MISMATCH = "dimension_mismatch"
INVALID_VECTOR = "invalid_vector"
NO_SUCH_TABLE = "no_such_table"
NO_SUCH_COLUMN = "no_such_column"
NO_SUCH_INDEX = "no_such_index"
TABLE_ALREADY_EXISTS = "table_already_exists"
INDEX_ALREADY_EXISTS = "index_already_exists"
TYPE_MISMATCH = "type_mismatch"
SYNTAX_ERROR = "syntax_error"
TRANSACTION_ERROR = "transaction_error"
CONSTRAINT_VIOLATION = "constraint_violation"
VALUE_OUT_OF_RANGE = "value_out_of_range"
UNSUPPORTED_OP = "unsupported_op"
INTERNAL_ERROR = "internal_error"


class SqlError(Exception):
    """A statement-level failure that must be reported as ok:false."""

    def __init__(self, code: str, message: str = "") -> None:
        super().__init__(message or code)
        self.code = code
        self.message = message or code


# ---------------------------------------------------------------- column types

BIGINT = "BIGINT"
VARCHAR = "VARCHAR"
VECTOR = "VECTOR"


class Column:
    __slots__ = ("name", "type", "length", "dim", "primary_key")

    def __init__(self, name: str, type_: str, length: int | None = None,
                 dim: int | None = None, primary_key: bool = False) -> None:
        self.name = name
        self.type = type_
        self.length = length
        self.dim = dim
        self.primary_key = primary_key

    def to_json(self) -> dict:
        out = {"name": self.name, "type": self.type, "primary_key": self.primary_key}
        if self.length is not None:
            out["length"] = self.length
        if self.dim is not None:
            out["dim"] = self.dim
        return out

    @staticmethod
    def from_json(raw: dict) -> "Column":
        return Column(raw["name"], raw["type"], raw.get("length"), raw.get("dim"),
                      bool(raw.get("primary_key")))


# ---------------------------------------------------------------- vectors

def parse_vector_literal(text: str) -> list[float]:
    """Parse '[0, 2]' / '[0.5,-1]' into floats.

    Raises SqlError(invalid_vector) for anything that is not a finite number.
    """
    body = text.strip()
    if body.startswith("[") and body.endswith("]"):
        body = body[1:-1]
    body = body.strip()
    if not body:
        raise SqlError(INVALID_VECTOR, "empty vector literal")
    values: list[float] = []
    for token in body.split(","):
        token = token.strip()
        if not token:
            raise SqlError(INVALID_VECTOR, "empty element in vector literal")
        try:
            value = float(token)
        except ValueError:
            raise SqlError(INVALID_VECTOR, f"not a number: {token!r}") from None
        if not math.isfinite(value):
            raise SqlError(INVALID_VECTOR, "vector contains NaN or Infinity")
        values.append(value)
    return values


# The largest finite float32.  ``array('f')`` silently turns an out-of-range
# double into ``inf`` instead of raising, so the range has to be checked
# before the value is packed.
FLOAT32_MAX = 3.4028234663852886e+38


def to_float32(values: Sequence[float]) -> array.array:
    """Store vectors with float32 semantics, as the task requires.

    A value that cannot be represented as a finite float32 is rejected:
    NaN, Infinity and doubles outside the float32 range are all errors.
    """
    out = array.array("f")
    for value in values:
        value = float(value)
        if not math.isfinite(value):
            raise SqlError(INVALID_VECTOR, "vector contains NaN or Infinity")
        if value > FLOAT32_MAX or value < -FLOAT32_MAX:
            raise SqlError(VALUE_OUT_OF_RANGE, "vector element overflows float32")
        out.append(value)
        if not math.isfinite(out[-1]):
            raise SqlError(VALUE_OUT_OF_RANGE, "vector element overflows float32")
    if len(out) != len(values):
        raise SqlError(VALUE_OUT_OF_RANGE, "vector element overflows float32")
    return out


def l2_distance(left: Sequence[float], right: Sequence[float]) -> float:
    """Euclidean distance (NOT the squared distance) in float64 over float32 inputs."""
    total = 0.0
    for a, b in zip(left, right):
        diff = float(a) - float(b)
        total += diff * diff
    return math.sqrt(total)


# ---------------------------------------------------------------- row encoding
#
# Frame layout (little endian):
#   [u32 payload_len][u8 kind][u64 txn_id][payload]
#
# Row payload:
#   [i64 pk]
#   per column: [u8 tag][value]
#     tag 0 NULL | 1 BIGINT [i64] | 2 VARCHAR [u32 len][utf8] | 3 VECTOR [u32 dim][f32 * dim]

KIND_INSERT = 1
KIND_UPDATE = 2
KIND_DELETE = 3
KIND_COMMIT = 4
KIND_ROLLBACK = 5

TAG_NULL = 0
TAG_BIGINT = 1
TAG_VARCHAR = 2
TAG_VECTOR = 3

_FRAME_HEADER = struct.Struct("<IQ")
_LENGTH = struct.Struct("<I")


def encode_value(column: Column, value: Any) -> bytes:
    if value is None:
        return bytes([TAG_NULL])
    if column.type == BIGINT:
        if isinstance(value, bool) or not isinstance(value, int):
            raise SqlError(TYPE_MISMATCH, f"column {column.name} expects BIGINT")
        return bytes([TAG_BIGINT]) + struct.pack("<q", value)
    if column.type == VARCHAR:
        if not isinstance(value, str):
            raise SqlError(TYPE_MISMATCH, f"column {column.name} expects VARCHAR")
        encoded = value.encode("utf-8")
        if column.length is not None and len(encoded) > column.length:
            raise SqlError(VALUE_OUT_OF_RANGE,
                           f"value too long for {column.name} VARCHAR({column.length})")
        return bytes([TAG_VARCHAR]) + _LENGTH.pack(len(encoded)) + encoded
    if column.type == VECTOR:
        if not isinstance(value, array.array):
            raise SqlError(INVALID_VECTOR, f"column {column.name} expects a vector")
        if column.dim is not None and len(value) != column.dim:
            raise SqlError(DIMENSION_MISMATCH,
                           f"column {column.name} expects dimension {column.dim}, got {len(value)}")
        return bytes([TAG_VECTOR]) + _LENGTH.pack(len(value)) + value.tobytes()
    raise SqlError(TYPE_MISMATCH, f"unsupported column type {column.type}")


def decode_value(column: Column, buf: memoryview, offset: int) -> tuple[Any, int]:
    tag = buf[offset]
    offset += 1
    if tag == TAG_NULL:
        return None, offset
    if tag == TAG_BIGINT:
        return struct.unpack_from("<q", buf, offset)[0], offset + 8
    if tag == TAG_VARCHAR:
        size = _LENGTH.unpack_from(buf, offset)[0]
        offset += 4
        text = bytes(buf[offset:offset + size]).decode("utf-8")
        return text, offset + size
    if tag == TAG_VECTOR:
        dim = _LENGTH.unpack_from(buf, offset)[0]
        offset += 4
        vec = array.array("f")
        vec.frombytes(bytes(buf[offset:offset + dim * 4]))
        return vec, offset + dim * 4
    raise SqlError(INTERNAL_ERROR, f"unknown value tag {tag}")


def encode_row(columns: Sequence[Column], pk: int, values: Sequence[Any]) -> bytes:
    out = bytearray()
    out += struct.pack("<q", pk)
    for column, value in zip(columns, values):
        out += encode_value(column, value)
    return bytes(out)


def decode_row(columns: Sequence[Column], buf: memoryview, offset: int) -> tuple[int, list[Any], int]:
    pk = struct.unpack_from("<q", buf, offset)[0]
    offset += 8
    values: list[Any] = []
    for column in columns:
        value, offset = decode_value(column, buf, offset)
        values.append(value)
    return pk, values, offset
