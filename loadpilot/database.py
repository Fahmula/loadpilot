import json
import logging
from datetime import date, datetime

import aiosqlite
from pydantic import BaseModel

from loadpilot.models import Charge, Load, LoadStatus, Stop, User

logger = logging.getLogger("loadpilot.database")

# Columns that update_load may write. Keys are interpolated into SQL, so never widen this to user input.
UPDATABLE_COLUMNS = frozenset(Load.model_fields) - {"id"}

# Append-only list of schema migrations; entry N upgrades the database to user_version N.
MIGRATIONS: list[list[str]] = [
    # 1: invoices get their own column instead of overwriting rate_con_pdf_path
    [
        "ALTER TABLE loads ADD COLUMN invoice_pdf_path TEXT",
        "UPDATE loads SET invoice_pdf_path = rate_con_pdf_path, rate_con_pdf_path = NULL "
        "WHERE rate_con_pdf_path LIKE 'static/invoices/%'",
    ],
    # 2: login account
    [
        """
        CREATE TABLE users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL UNIQUE COLLATE NOCASE,
            password_hash TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """,
    ],
    # 3: timestamps for the dashboard's recent activity
    [
        "ALTER TABLE loads ADD COLUMN created_at TEXT",
        "ALTER TABLE loads ADD COLUMN invoiced_at TEXT",
    ],
    # 4: AI settings per provider; keep an existing Gemini key on the model it was already using
    [
        "UPDATE settings SET key = 'ai_api_key_gemini' WHERE key = 'gemini_api_key'",
        "INSERT OR IGNORE INTO settings (key, value) SELECT 'ai_model_gemini', 'gemini-3.1-flash-lite' "
        "WHERE EXISTS (SELECT 1 FROM settings WHERE key = 'ai_api_key_gemini')",
    ],
    # 5: deadhead tracking
    [
        "ALTER TABLE loads ADD COLUMN empty_miles INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE loads ADD COLUMN deadhead_origin TEXT",
    ],
    # 6: broker payments
    [
        "ALTER TABLE loads ADD COLUMN paid_at TEXT",
        "ALTER TABLE loads ADD COLUMN amount_paid REAL",
    ],
    # 7: extra charges (detention, lumper, TONU...) as a JSON list of {description, amount}
    [
        "ALTER TABLE loads ADD COLUMN charges TEXT NOT NULL DEFAULT '[]'",
    ],
    # 8: stops between the first pickup and the final delivery, as a JSON list of {kind, location, stop_date}
    [
        "ALTER TABLE loads ADD COLUMN stops TEXT NOT NULL DEFAULT '[]'",
    ],
]


# Load fields stored as a JSON list of models
JSON_COLUMNS: dict[str, type[BaseModel]] = {"charges": Charge, "stops": Stop}


def json_list(column: str, items: list | None) -> str:
    model = JSON_COLUMNS[column]
    return json.dumps([model.model_validate(item).model_dump(mode="json") for item in items or []])


class DatabaseManager:
    def __init__(self, db_path: str = "loadpilot.db"):
        self.db_path = db_path

    async def init_db(self):
        """Creates the LoadPilot tables if needed, then applies any pending migrations."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("""
                CREATE TABLE IF NOT EXISTS loads (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    invoice_number TEXT UNIQUE,
                    broker_name TEXT NOT NULL,
                    broker_email TEXT,
                    load_reference TEXT NOT NULL,
                    rate_amount REAL NOT NULL,
                    pickup_location TEXT NOT NULL,
                    pickup_date TEXT NOT NULL,
                    delivery_location TEXT NOT NULL,
                    delivery_date TEXT NOT NULL,
                    actual_delivery_date TEXT,
                    loaded_miles INTEGER NOT NULL DEFAULT 0,
                    status TEXT NOT NULL,
                    notes TEXT,
                    rate_con_pdf_path TEXT,
                    bol_pdf_path TEXT
                )
            """)

            await db.execute("""
                CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY,
                    value TEXT
                )
            """)
            await self._migrate(db)
            await db.commit()
            logger.info("Database initialized successfully.")

    async def _migrate(self, db: aiosqlite.Connection):
        """Applies each migration newer than the database's PRAGMA user_version, in order.

        A migration and its version bump commit together or not at all (SQLite's ALTER TABLE is transactional):
        if one fails or the app stops part-way, the next start retries it from a clean slate, instead of
        failing on a column the half-done attempt already added.
        """
        async with db.execute("PRAGMA user_version") as cursor:
            current_version = (await cursor.fetchone())[0]

        for version, statements in enumerate(MIGRATIONS[current_version:], start=current_version + 1):
            await db.execute("BEGIN")
            try:
                for statement in statements:
                    await db.execute(statement)
                await db.execute(f"PRAGMA user_version = {version}")
                await db.commit()
            except BaseException:
                await db.rollback()
                raise
            logger.info(f"Applied database migration {version}.")

    # -----------------------------------------
    # SETTINGS (Key-Value Store Pattern)
    # -----------------------------------------
    async def save_setting(self, key: str, value: str):
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, str(value)))
            await db.commit()

    async def get_setting(self, key: str, default: str = "") -> str:
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute("SELECT value FROM settings WHERE key = ?", (key,)) as cursor:
                row = await cursor.fetchone()
                res = row[0] if row else default
                return str(res) if res is not None else ""

    async def get_all_settings(self) -> dict[str | bool, str]:
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute("SELECT key, value FROM settings") as cursor:
                rows = await cursor.fetchall()
                # Cast string settings to booleans where applicable for the templates
                settings = {}
                for row in rows:
                    key, val = row[0], row[1]
                    if val.lower() == "true":
                        settings[key] = True
                    elif val.lower() == "false":
                        settings[key] = False
                    else:
                        settings[key] = val
                return settings

    # -----------------------------------------
    # USERS
    # -----------------------------------------
    async def has_users(self) -> bool:
        async with aiosqlite.connect(self.db_path) as db, db.execute("SELECT 1 FROM users LIMIT 1") as cursor:
            return await cursor.fetchone() is not None

    async def create_user(self, username: str, password_hash: str) -> User:
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                "INSERT INTO users (username, password_hash) VALUES (?, ?)", (username, password_hash)
            )
            await db.commit()
            return User(id=cursor.lastrowid, username=username, password_hash=password_hash)

    async def get_user(self, user_id: int) -> User | None:
        return await self._fetch_user("SELECT id, username, password_hash FROM users WHERE id = ?", user_id)

    async def get_user_by_username(self, username: str) -> User | None:
        return await self._fetch_user("SELECT id, username, password_hash FROM users WHERE username = ?", username)

    async def list_users(self) -> list[User]:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("SELECT id, username, password_hash FROM users ORDER BY id") as cursor:
                return [User(**dict(row)) for row in await cursor.fetchall()]

    async def set_password_hash(self, user_id: int, password_hash: str):
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("UPDATE users SET password_hash = ? WHERE id = ?", (password_hash, user_id))
            await db.commit()

    async def _fetch_user(self, query: str, value: int | str) -> User | None:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(query, (value,)) as cursor:
                row = await cursor.fetchone()
                return User(**dict(row)) if row else None

    # -----------------------------------------
    # LOADS PERSISTENCE
    # -----------------------------------------
    async def add_load(self, load: Load) -> Load:
        load.created_at = load.created_at or datetime.now().replace(microsecond=0)
        async with aiosqlite.connect(self.db_path) as db:
            actual_del = load.actual_delivery_date.isoformat() if load.actual_delivery_date else None
            cursor = await db.execute(
                """INSERT INTO loads (
                    invoice_number, broker_name, broker_email, load_reference,
                    rate_amount, pickup_location, pickup_date, delivery_location,
                    delivery_date, actual_delivery_date, loaded_miles, empty_miles, deadhead_origin, status,
                    notes, rate_con_pdf_path, bol_pdf_path, invoice_pdf_path, created_at, invoiced_at, paid_at,
                    amount_paid, charges, stops
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    load.invoice_number,
                    load.broker_name,
                    load.broker_email,
                    load.load_reference,
                    load.rate_amount,
                    load.pickup_location,
                    load.pickup_date.isoformat(),
                    load.delivery_location,
                    load.delivery_date.isoformat(),
                    actual_del,
                    load.loaded_miles,
                    load.empty_miles,
                    load.deadhead_origin,
                    load.status.value,
                    load.notes,
                    load.rate_con_pdf_path,
                    load.bol_pdf_path,
                    load.invoice_pdf_path,
                    load.created_at.isoformat(),
                    load.invoiced_at.isoformat() if load.invoiced_at else None,
                    load.paid_at.isoformat() if load.paid_at else None,
                    load.amount_paid,
                    json_list("charges", load.charges),
                    json_list("stops", load.stops),
                ),
            )
            await db.commit()
            load.id = cursor.lastrowid
            return load

    async def get_load(self, load_id: int) -> Load | None:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("SELECT * FROM loads WHERE id = ?", (load_id,)) as cursor:
                row = await cursor.fetchone()
                if row:
                    return self._row_to_load(row)
                return None

    async def get_loads(
        self, skip: int = 0, limit: int = 100, status: LoadStatus | None = None, search_query: str | None = None
    ) -> list[Load]:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            conditions = []
            params = []

            if status:
                conditions.append("status = ?")
                params.append(status.value)

            if search_query:
                conditions.append(
                    "(broker_name LIKE ? OR load_reference LIKE ? OR invoice_number LIKE ? "
                    "OR pickup_location LIKE ? OR delivery_location LIKE ? OR stops LIKE ?)"
                )
                search_param = f"%{search_query}%"
                params.extend([search_param] * 6)

            where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""
            query = f"SELECT * FROM loads {where_clause} ORDER BY pickup_date DESC LIMIT ? OFFSET ?"
            params.extend([limit, skip])

            async with db.execute(query, params) as cursor:
                rows = await cursor.fetchall()
                return [self._row_to_load(row) for row in rows]

    async def update_load(self, load_id: int, update_data: dict[str, str | int | date | LoadStatus]) -> Load | None:
        if not update_data:
            return await self.get_load(load_id)

        unknown_columns = set(update_data) - UPDATABLE_COLUMNS
        if unknown_columns:
            raise ValueError(f"Cannot update unknown load fields: {sorted(unknown_columns)}")

        set_clauses = []
        params = []
        for key, val in update_data.items():
            set_clauses.append(f"{key} = ?")
            if isinstance(val, (date, datetime)):
                params.append(val.isoformat())
            elif isinstance(val, LoadStatus):
                params.append(val.value)
            elif key in JSON_COLUMNS:
                params.append(json_list(key, val))
            else:
                params.append(val)

        params.append(load_id)
        query = f"UPDATE loads SET {', '.join(set_clauses)} WHERE id = ?"

        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(query, params)
            await db.commit()

        return await self.get_load(load_id)

    async def delete_load(self, load_id: int) -> bool:
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute("DELETE FROM loads WHERE id = ?", (load_id,))
            await db.commit()
            return cursor.rowcount > 0

    def _row_to_load(self, row: aiosqlite.Row) -> Load:
        actual_del_val = row["actual_delivery_date"]
        actual_delivery_date = date.fromisoformat(actual_del_val) if actual_del_val else None

        return Load(
            id=row["id"],
            invoice_number=row["invoice_number"],
            broker_name=row["broker_name"],
            broker_email=row["broker_email"],
            load_reference=row["load_reference"],
            rate_amount=row["rate_amount"],
            pickup_location=row["pickup_location"],
            pickup_date=date.fromisoformat(row["pickup_date"]),
            delivery_location=row["delivery_location"],
            delivery_date=date.fromisoformat(row["delivery_date"]),
            actual_delivery_date=actual_delivery_date,
            loaded_miles=row["loaded_miles"],
            empty_miles=row["empty_miles"],
            deadhead_origin=row["deadhead_origin"],
            status=LoadStatus(row["status"]),
            notes=row["notes"],
            rate_con_pdf_path=row["rate_con_pdf_path"],
            bol_pdf_path=row["bol_pdf_path"],
            invoice_pdf_path=row["invoice_pdf_path"],
            created_at=datetime.fromisoformat(row["created_at"]) if row["created_at"] else None,
            invoiced_at=datetime.fromisoformat(row["invoiced_at"]) if row["invoiced_at"] else None,
            paid_at=date.fromisoformat(row["paid_at"]) if row["paid_at"] else None,
            amount_paid=row["amount_paid"],
            charges=json.loads(row["charges"]),
            stops=json.loads(row["stops"]),
        )

    async def get_earned_loads(self) -> list[Load]:
        """Loads that have actually been delivered (Delivered, Invoiced or Paid); the basis for analytics."""
        return await self._fetch_loads(
            "SELECT * FROM loads WHERE status IN (?, ?, ?)",
            (LoadStatus.DELIVERED, LoadStatus.INVOICED, LoadStatus.PAID),
        )

    async def get_unpaid_invoices(self) -> list[Load]:
        """Invoiced loads still waiting on the broker's payment, oldest invoice first."""
        return await self._fetch_loads(
            "SELECT * FROM loads WHERE status = ? ORDER BY invoiced_at, id", (LoadStatus.INVOICED,)
        )

    async def get_broker_contacts(self) -> list[tuple[str, str | None]]:
        """(broker name, email) from every load, newest first."""
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute("SELECT broker_name, broker_email FROM loads ORDER BY id DESC") as cursor:
                return [tuple(row) for row in await cursor.fetchall()]

    async def get_document_paths(self) -> set[str]:
        """The web path of every rate con, BOL and invoice a load links to."""
        async with aiosqlite.connect(self.db_path) as db:
            query = "SELECT rate_con_pdf_path, bol_pdf_path, invoice_pdf_path FROM loads"
            async with db.execute(query) as cursor:
                return {path for row in await cursor.fetchall() for path in row if path}

    async def get_load_by_invoice_number(self, invoice_number: str) -> Load | None:
        loads = await self._fetch_loads("SELECT * FROM loads WHERE invoice_number = ?", (invoice_number,))
        return loads[0] if loads else None

    async def get_recent_loads(self, limit: int = 20) -> list[Load]:
        """Most recently added loads first."""
        return await self._fetch_loads("SELECT * FROM loads ORDER BY id DESC LIMIT ?", (limit,))

    async def count_loads_by_status(self) -> dict[LoadStatus, int]:
        counts = dict.fromkeys(LoadStatus, 0)
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute("SELECT status, COUNT(*) FROM loads GROUP BY status") as cursor:
                for status, count in await cursor.fetchall():
                    counts[LoadStatus(status)] = count
        return counts

    async def _fetch_loads(self, query: str, params: tuple) -> list[Load]:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(query, params) as cursor:
                return [self._row_to_load(row) for row in await cursor.fetchall()]
