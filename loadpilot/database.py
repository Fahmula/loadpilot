import logging
from datetime import date, datetime

import aiosqlite

from loadpilot.models import Load, LoadStatus, User

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
]


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
        """Applies each migration newer than the database's PRAGMA user_version, in order."""
        async with db.execute("PRAGMA user_version") as cursor:
            current_version = (await cursor.fetchone())[0]

        for version, statements in enumerate(MIGRATIONS[current_version:], start=current_version + 1):
            for statement in statements:
                await db.execute(statement)
            await db.execute(f"PRAGMA user_version = {version}")
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
        async with aiosqlite.connect(self.db_path) as db:
            actual_del = load.actual_delivery_date.isoformat() if load.actual_delivery_date else None
            cursor = await db.execute(
                """INSERT INTO loads (
                    invoice_number, broker_name, broker_email, load_reference,
                    rate_amount, pickup_location, pickup_date, delivery_location,
                    delivery_date, actual_delivery_date, loaded_miles, status, notes,
                    rate_con_pdf_path, bol_pdf_path, invoice_pdf_path
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
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
                    load.status.value,
                    load.notes,
                    load.rate_con_pdf_path,
                    load.bol_pdf_path,
                    load.invoice_pdf_path,
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
                    "OR pickup_location LIKE ? OR delivery_location LIKE ?)"
                )
                search_param = f"%{search_query}%"
                params.extend([search_param] * 5)

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
            status=LoadStatus(row["status"]),
            notes=row["notes"],
            rate_con_pdf_path=row["rate_con_pdf_path"],
            bol_pdf_path=row["bol_pdf_path"],
            invoice_pdf_path=row["invoice_pdf_path"],
        )

    async def get_analytics_data(self) -> dict:
        """Aggregates rates, miles, RPM, and broker data for analytics.

        Only loads that have actually been delivered (Delivered or Invoiced) count as earned.
        """
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM loads WHERE status IN (?, ?)", (LoadStatus.DELIVERED, LoadStatus.INVOICED)
            ) as cursor:
                rows = await cursor.fetchall()

            today = date.today()
            current_year = today.year
            current_month = today.month

            # Iso-week calendar metrics for current week groupings
            current_year_iso, current_week_iso, _ = today.isocalendar()

            total_revenue = 0.0
            total_miles = 0

            ytd_revenue = 0.0
            ytd_miles = 0

            monthly_revenue = 0.0
            monthly_miles = 0

            weekly_revenue = 0.0
            weekly_miles = 0

            broker_map = {}  # broker_name -> {"revenue": X, "miles": Y, "load_count": Z}

            for row in rows:
                rate = row["rate_amount"]
                miles = row["loaded_miles"] or 0
                broker = row["broker_name"]

                delivery_date_str = row["actual_delivery_date"] or row["delivery_date"]
                delivery_date = date.fromisoformat(delivery_date_str)

                # Global totals
                total_revenue += rate
                total_miles += miles

                # YTD Metrics
                if delivery_date.year == current_year:
                    ytd_revenue += rate
                    ytd_miles += miles

                # Monthly Metrics
                if delivery_date.year == current_year and delivery_date.month == current_month:
                    monthly_revenue += rate
                    monthly_miles += miles

                # Weekly Metrics
                py_year, py_week, _ = delivery_date.isocalendar()
                if py_year == current_year_iso and py_week == current_week_iso:
                    weekly_revenue += rate
                    weekly_miles += miles

                # Broker aggregation
                if broker not in broker_map:
                    broker_map[broker] = {"revenue": 0.0, "miles": 0, "load_count": 0}
                broker_map[broker]["revenue"] += rate
                broker_map[broker]["miles"] += miles
                broker_map[broker]["load_count"] += 1

            # Safely calculate RPM
            def calc_rpm(rev: float, mi: int) -> float:
                return rev / mi if mi > 0 else 0.0

            # Broker list sorted by revenue descending
            broker_list = []
            for b_name, stats in broker_map.items():
                stats["rpm"] = calc_rpm(stats["revenue"], stats["miles"])
                stats["name"] = b_name
                broker_list.append(stats)
            broker_list.sort(key=lambda x: x["revenue"], reverse=True)

            return {
                "total_revenue": total_revenue,
                "total_miles": total_miles,
                "global_rpm": calc_rpm(total_revenue, total_miles),
                "ytd_revenue": ytd_revenue,
                "ytd_miles": ytd_miles,
                "ytd_rpm": calc_rpm(ytd_revenue, ytd_miles),
                "monthly_revenue": monthly_revenue,
                "monthly_miles": monthly_miles,
                "monthly_rpm": calc_rpm(monthly_revenue, monthly_miles),
                "weekly_revenue": weekly_revenue,
                "weekly_miles": weekly_miles,
                "weekly_rpm": calc_rpm(weekly_revenue, weekly_miles),
                "brokers": broker_list[:5],
            }
