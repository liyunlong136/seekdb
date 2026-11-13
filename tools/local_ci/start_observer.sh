#!/usr/bin/env bash
# Start a local seekdb observer and wait until it accepts connections.
#
# seekdb is configured through command line options, the configuration file
# is looked up under <base-dir>/etc.  The memory budget and the cpu count are
# derived from the machine, so no parameter has to be given.
#
# Usage: tools/local_ci/start_observer.sh [binary-dir]
set -euo pipefail

BIN_DIR="${1:-observer}"
BASE_DIR="${SEEKDB_BASE_DIR:-/tmp/seekdb_home}"
DATA_DIR="${SEEKDB_DATA_DIR:-/tmp/seekdb_data}"
REDO_DIR="${SEEKDB_REDO_DIR:-/tmp/seekdb_redo}"
PORT="${SEEKDB_PORT:-2881}"

mkdir -p "$BASE_DIR" "$DATA_DIR" "$REDO_DIR"
install -m 0755 "$BIN_DIR/seekdb" /usr/local/bin/seekdb
if [[ -f "$BIN_DIR/observer" ]]; then
  install -m 0755 "$BIN_DIR/observer" /usr/local/bin/observer
fi

nohup /usr/local/bin/seekdb --nodaemon --port "$PORT" \
  --base-dir "$BASE_DIR" --data-dir "$DATA_DIR" --redo-dir "$REDO_DIR" \
  --log-level WARN > /tmp/seekdb.log 2>&1 &

if ! python3 - "$PORT" <<'PY'
import sys
import time

import pymysql

port = int(sys.argv[1])
for waited in range(300):
    try:
        connection = pymysql.connect(host="127.0.0.1", port=port, user="root",
                                     connect_timeout=3)
        connection.close()
        print("observer is up after %ds" % waited)
        sys.exit(0)
    except Exception:
        time.sleep(1)
print("observer did not come up", file=sys.stderr)
sys.exit(1)
PY
then
  echo "----- /tmp/seekdb.log -----" >&2
  tail -100 /tmp/seekdb.log >&2 || true
  echo "----- $BASE_DIR/log -----" >&2
  ls -la "$BASE_DIR/log" >&2 || true
  for logfile in "$BASE_DIR"/log/*.log; do
    [[ -f "$logfile" ]] || continue
    echo "----- $logfile -----" >&2
    tail -60 "$logfile" >&2 || true
  done
  exit 1
fi
