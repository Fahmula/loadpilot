import aiosqlite
import logging
from datetime import date, datetime
from loadpilot.models import Load, LoadStatus

logger = logging.getLogger("loadpilot.database")

class DatabaseManager:
    def __init__(self, db_path: str = "loadpilot.db"):
        self.db_path = db_path

    async def init_db(self):
        """Creates the necessary tables for LoadPilot if they don't exist yet."""
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
            await db.commit()
            logger.info("Database initialized successfully.")

    # -----------------------------------------
    # SETTINGS (Key-Value Store Pattern)
    # -----------------------------------------
    async def save_setting(self, key: str, value: str):
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
                (key, str(value))
            )
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
    # LOADS PERSISTENCE
    # -----------------------------------------
    async def add_load(self, load: Load) -> Load:
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                """INSERT INTO loads (
                    invoice_number, broker_name, broker_email, load_reference, 
                    rate_amount, pickup_location, pickup_date, delivery_location, 
                    delivery_date, loaded_miles, status, notes, rate_con_pdf_path, bol_pdf_path
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    load.invoice_number, load.broker_name, load.broker_email, load.load_reference,
                    load.rate_amount, load.pickup_location, load.pickup_date.isoformat(), load.delivery_location,
                    load.delivery_date.isoformat(), load.loaded_miles, load.status.value, load.notes,
                    load.rate_con_pdf_path, load.bol_pdf_path
                )
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

    async def get_loads(self, skip: int = 0, limit: int = 100, status: LoadStatus | None = None) -> list[Load]:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            if status:
                query = "SELECT * FROM loads WHERE status = ? ORDER BY pickup_date DESC LIMIT ? OFFSET ?"
                params = (status.value, limit, skip)
            else:
                query = "SELECT * FROM loads ORDER BY pickup_date DESC LIMIT ? OFFSET ?"
                params = (limit, skip)
                
            async with db.execute(query, params) as cursor:
                rows = await cursor.fetchall()
                return [self._row_to_load(row) for row in rows]

    async def update_load(self, load_id: int, update_data: dict[str, str | int | date | LoadStatus]) -> Load | None:
        if not update_data:
            return await self.get_load(load_id)

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
            loaded_miles=row["loaded_miles"],
            status=LoadStatus(row["status"]),
            notes=row["notes"],
            rate_con_pdf_path=row["rate_con_pdf_path"],
            bol_pdf_path=row["bol_pdf_path"]
        )