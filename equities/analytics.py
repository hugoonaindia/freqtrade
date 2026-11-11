"""Persistent research ledger. Credentials and request headers are never recorded."""

import hashlib
import json
import sqlite3
import uuid
import zlib
import shutil
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from .execution_quality import execution_quality


REDACTED = {
    "key",
    "secret",
    "api_key",
    "api_secret",
    "authorization",
    "headers",
    "password",
    "token",
    "apca_api_key_id",
    "apca_api_secret_key",
}
ORDER_FIELDS = {
    "id",
    "client_order_id",
    "symbol",
    "side",
    "type",
    "order_class",
    "qty",
    "filled_qty",
    "filled_avg_price",
    "limit_price",
    "stop_price",
    "time_in_force",
    "status",
    "created_at",
    "submitted_at",
    "updated_at",
    "filled_at",
    "canceled_at",
    "expired_at",
    "group_id",
    "group_role",
}
TERMINAL = {"filled", "canceled", "expired", "rejected", "replaced"}


def clean(value):
    if isinstance(value, dict):
        return {str(k): clean(v) for k, v in value.items() if str(k).lower() not in REDACTED}
    if isinstance(value, (list, tuple)):
        return [clean(v) for v in value]
    return value


def encoded(value):
    return json.dumps(clean(value), sort_keys=True, ensure_ascii=False, allow_nan=False).encode()


class Analytics:
    def __init__(self, path, *, reserve_bytes=0):
        self.path = Path(path)
        self.reserve_bytes = reserve_bytes
        self.path.parent.mkdir(parents=True, exist_ok=True)
        source = Path(__file__).parent
        digest = hashlib.sha256()
        for filename in sorted(source.glob("*.py")):
            digest.update(filename.name.encode())
            digest.update(filename.read_bytes())
        self.code_hash = digest.hexdigest()
        with self.connection() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript("""
                CREATE TABLE IF NOT EXISTS runs (
                    id TEXT PRIMARY KEY, kind TEXT NOT NULL, created_at TEXT NOT NULL,
                    code_hash TEXT NOT NULL, config_json TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY, run_id TEXT NOT NULL, occurred_at TEXT NOT NULL,
                    kind TEXT NOT NULL, symbol TEXT, payload_json TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS events_run ON events(run_id,id);
                CREATE TABLE IF NOT EXISTS datasets (
                    sha256 TEXT PRIMARY KEY, created_at TEXT NOT NULL, compressed_json BLOB NOT NULL);
                CREATE TABLE IF NOT EXISTS fills (run_id TEXT NOT NULL, activity_id TEXT NOT NULL, payload_json TEXT NOT NULL, PRIMARY KEY(run_id,activity_id));
                CREATE TABLE IF NOT EXISTS execution_context (run_id TEXT NOT NULL, order_key TEXT NOT NULL, payload_json TEXT NOT NULL, PRIMARY KEY(run_id,order_key));
                CREATE TABLE IF NOT EXISTS orders (
                    run_id TEXT NOT NULL, order_key TEXT NOT NULL, status TEXT NOT NULL,
                    payload_json TEXT NOT NULL, PRIMARY KEY(run_id,order_key));
                CREATE INDEX IF NOT EXISTS orders_broker_id ON orders(run_id,json_extract(payload_json,'$.id'));
            """)

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.path, timeout=5)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA busy_timeout=5000")
        try:
            with db:
                yield db
        finally:
            db.close()

    def start(self, kind, config):
        run_id = uuid.uuid4().hex
        with self.connection() as db:
            db.execute(
                "INSERT INTO runs VALUES (?,?,?,?,?)",
                (
                    run_id,
                    kind,
                    datetime.now(UTC).isoformat(),
                    self.code_hash,
                    encoded(config).decode(),
                ),
            )
        return run_id

    def record(self, run_id, kind, payload, symbol=None):
        with self.connection() as db:
            db.execute(
                "INSERT INTO events(run_id,occurred_at,kind,symbol,payload_json) VALUES(?,?,?,?,?)",
                (run_id, datetime.now(UTC).isoformat(), kind, symbol, encoded(payload).decode()),
            )

    def dataset(self, payload):
        data = encoded(payload)
        fingerprint = hashlib.sha256(data).hexdigest()
        compressed = zlib.compress(data)
        if self.reserve_bytes and shutil.disk_usage(
            self.path.parent
        ).free < self.reserve_bytes + 2 * len(compressed):
            raise ValueError(
                "Espacio insuficiente para archivar investigación sin consumir el margen del supervisor. Libera espacio antes de repetir."
            )
        with self.connection() as db:
            exists = db.execute("SELECT 1 FROM datasets WHERE sha256=?", (fingerprint,)).fetchone()
            if not exists:
                db.execute(
                    "INSERT INTO datasets VALUES (?,?,?)",
                    (fingerprint, datetime.now(UTC).isoformat(), compressed),
                )
        return fingerprint

    def restore_dataset(self, fingerprint):
        with self.connection() as db:
            row = db.execute(
                "SELECT compressed_json FROM datasets WHERE sha256=?", (fingerprint,)
            ).fetchone()
        if row is None:
            raise ValueError("Datos no disponibles.")
        data = zlib.decompress(row[0])
        if hashlib.sha256(data).hexdigest() != fingerprint:
            raise ValueError("El archivo de datos no supera la comprobación de integridad.")
        return json.loads(data)

    def order(self, run_id, order):
        payload = {key: order[key] for key in ORDER_FIELDS if key in order}
        order_key = payload.get("client_order_id") or payload.get("id")
        if not order_key:
            return False
        status = payload.get("status", "submitted")
        text = encoded(payload).decode()
        with self.connection() as db:
            # A close response may initially expose only a broker id. Consolidate once
            # the subsequent read adds its client id, avoiding an orphan pending row.
            if payload.get("id") and payload.get("client_order_id") and payload["id"] != order_key:
                db.execute(
                    "DELETE FROM orders WHERE run_id=? AND order_key=?", (run_id, payload["id"])
                )
                old_context = db.execute(
                    "SELECT payload_json FROM execution_context WHERE run_id=? AND order_key=?",
                    (run_id, payload["id"]),
                ).fetchone()
                if old_context:
                    new_context = db.execute(
                        "SELECT payload_json FROM execution_context WHERE run_id=? AND order_key=?",
                        (run_id, order_key),
                    ).fetchone()
                    merged = {
                        **json.loads(old_context[0]),
                        **(json.loads(new_context[0]) if new_context else {}),
                    }
                    db.execute(
                        "INSERT INTO execution_context VALUES(?,?,?) ON CONFLICT(run_id,order_key) DO UPDATE SET payload_json=excluded.payload_json",
                        (run_id, order_key, encoded(merged).decode()),
                    )
                    db.execute(
                        "DELETE FROM execution_context WHERE run_id=? AND order_key=?",
                        (run_id, payload["id"]),
                    )
            previous = db.execute(
                "SELECT payload_json FROM orders WHERE run_id=? AND order_key=?",
                (run_id, order_key),
            ).fetchone()
            changed = not previous or previous[0] != text
            if changed:
                db.execute(
                    "INSERT INTO orders VALUES(?,?,?,?) ON CONFLICT(run_id,order_key) "
                    "DO UPDATE SET status=excluded.status,payload_json=excluded.payload_json",
                    (run_id, order_key, status, text),
                )
                db.execute(
                    "INSERT INTO events(run_id,occurred_at,kind,symbol,payload_json) VALUES(?,?,?,?,?)",
                    (run_id, datetime.now(UTC).isoformat(), "order", payload.get("symbol"), text),
                )
        for leg in order.get("legs") or []:
            changed = self.order(run_id, leg) or changed
        return changed

    def execution_context(self, run_id, order_key, payload):
        with self.connection() as db:
            row = db.execute(
                "SELECT payload_json FROM execution_context WHERE run_id=? AND order_key=?",
                (run_id, order_key),
            ).fetchone()
            merged = {**(json.loads(row[0]) if row else {}), **clean(payload)}
            db.execute(
                "INSERT INTO execution_context VALUES(?,?,?) ON CONFLICT(run_id,order_key) DO UPDATE SET payload_json=excluded.payload_json",
                (run_id, order_key, encoded(merged).decode()),
            )

    def fill(self, run_id, activity):
        fields = {
            "id",
            "order_id",
            "symbol",
            "side",
            "qty",
            "price",
            "transaction_time",
            "type",
            "cum_qty",
            "leaves_qty",
        }
        payload = {key: activity[key] for key in fields if key in activity}
        if (
            not payload.get("id")
            or not payload.get("order_id")
            or not payload.get("transaction_time")
        ):
            raise ValueError("Fill sin identificador, orden o instante de ejecución.")
        text = encoded(payload).decode()
        with self.connection() as db:
            previous = db.execute(
                "SELECT payload_json FROM fills WHERE run_id=? AND activity_id=?",
                (run_id, payload["id"]),
            ).fetchone()
            if not previous or previous[0] != text:
                db.execute(
                    "INSERT INTO fills VALUES(?,?,?) ON CONFLICT(run_id,activity_id) DO UPDATE SET payload_json=excluded.payload_json",
                    (run_id, payload["id"], text),
                )
                db.execute(
                    "INSERT INTO events(run_id,occurred_at,kind,symbol,payload_json) VALUES(?,?,?,?,?)",
                    (run_id, datetime.now(UTC).isoformat(), "fill", payload.get("symbol"), text),
                )

    def fills(self, run_id):
        if not run_id:
            return []
        with self.connection() as db:
            rows = db.execute(
                "SELECT f.payload_json,o.payload_json,c.payload_json FROM fills f LEFT JOIN orders o ON o.run_id=f.run_id AND json_extract(o.payload_json,'$.id')=json_extract(f.payload_json,'$.order_id') LEFT JOIN execution_context c ON c.run_id=o.run_id AND c.order_key=o.order_key WHERE f.run_id=? ORDER BY json_extract(f.payload_json,'$.transaction_time') DESC LIMIT 100",
                (run_id,),
            ).fetchall()
        result = []
        for row in rows:
            fill = json.loads(row[0])
            order = json.loads(row[1]) if row[1] else {}
            context = json.loads(row[2]) if row[2] else {}
            quality = execution_quality(
                context,
                {
                    **order,
                    "side": fill.get("side"),
                    "filled_at": fill.get("transaction_time"),
                    "filled_avg_price": fill.get("price"),
                    "filled_qty": fill.get("qty"),
                },
            )
            result.append(
                {
                    **fill,
                    **{
                        key: quality.get(key)
                        for key in (
                            "fill_delay_seconds",
                            "slippage_bps",
                            "spread_bps",
                            "filled_after_deadline",
                        )
                    },
                }
            )
        return result

    def executions(self, run_id):
        if not run_id:
            return []
        with self.connection() as db:
            rows = db.execute(
                "SELECT c.payload_json,o.payload_json FROM execution_context c LEFT JOIN orders o ON o.run_id=c.run_id AND o.order_key=c.order_key WHERE c.run_id=? ORDER BY c.rowid DESC LIMIT 100",
                (run_id,),
            ).fetchall()
        return [
            execution_quality(json.loads(row[0]), json.loads(row[1]) if row[1] else {})
            for row in rows
        ]

    def pending(self, run_id):
        with self.connection() as db:
            rows = db.execute(
                "SELECT status,payload_json FROM orders WHERE run_id=?", (run_id,)
            ).fetchall()
        return [json.loads(row["payload_json"]) for row in rows if row["status"] not in TERMINAL]

    def finish_backtest(self, run_id, inputs, report):
        provenance = {
            "run_id": run_id,
            "input_sha256": self.dataset(inputs),
            "code_sha256": self.code_hash,
            "schema_version": 1,
            "generated_at": datetime.now(UTC).isoformat(),
        }
        report["provenance"] = provenance
        fingerprint = self.dataset(report)
        self.record(run_id, "backtest_complete", {"report_sha256": fingerprint, **provenance})
        return provenance

    def summary(self, run_id=None):
        with self.connection() as db:
            counts = {
                table: db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                for table in ("runs", "events", "datasets", "orders")
            }
            events = []
            if run_id:
                events = [
                    dict(row)
                    for row in db.execute(
                        "SELECT id,occurred_at,kind,symbol,payload_json FROM events "
                        "WHERE run_id=? ORDER BY id DESC LIMIT 100",
                        (run_id,),
                    )
                ]
        for event in events:
            event["payload"] = json.loads(event.pop("payload_json"))
        return {
            "enabled": True,
            "run_id": run_id,
            "counts": counts,
            "events": events,
            "code_sha256": self.code_hash,
        }
