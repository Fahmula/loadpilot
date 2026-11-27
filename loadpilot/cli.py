"""Admin commands, run inside the container.

In Docker:  docker exec -it loadpilot python -m loadpilot.cli reset-password
            docker exec -it loadpilot python -m loadpilot.cli clean-documents [--delete]
"""

import argparse
import asyncio
import getpass
import sys
from pathlib import Path

from loadpilot.auth import MIN_PASSWORD_LENGTH, hash_password
from loadpilot.config import DB_PATH
from loadpilot.database import DatabaseManager
from loadpilot.documents import orphaned_documents


async def reset_password(db: DatabaseManager, password: str, username: str | None = None) -> str:
    """Sets a new password and returns the username it was set for."""
    users = await db.list_users()
    if not users:
        raise SystemExit("No account exists yet. Open LoadPilot in your browser to create one.")

    if username:
        user = next((u for u in users if u.username.lower() == username.lower()), None)
        if not user:
            raise SystemExit(f"No account named '{username}'.")
    elif len(users) == 1:
        user = users[0]
    else:
        raise SystemExit("Several accounts exist; pass --username.")

    if len(password) < MIN_PASSWORD_LENGTH:
        raise SystemExit(f"Password must be at least {MIN_PASSWORD_LENGTH} characters.")

    await db.set_password_hash(user.id, hash_password(password))
    return user.username


async def clean_documents(db: DatabaseManager, delete: bool = False) -> list[Path]:
    """Rate cons, BOLs and invoices no load links to; deleted too when delete is set."""
    orphans = orphaned_documents(await db.get_document_paths())
    if delete:
        for path in orphans:
            path.unlink(missing_ok=True)
    return orphans


def main(argv: list[str] | None = None):
    parser = argparse.ArgumentParser(prog="python -m loadpilot.cli")
    commands = parser.add_subparsers(dest="command", required=True)
    reset = commands.add_parser("reset-password", help="Set a new login password (signs out all devices)")
    reset.add_argument("--username", help="Account to reset (only needed if there are several)")
    clean = commands.add_parser("clean-documents", help="List document files no load links to")
    clean.add_argument("--delete", action="store_true", help="Delete them instead of only listing them")
    args = parser.parse_args(argv)

    if args.command == "reset-password":
        password = getpass.getpass("New password: ")
        if password != getpass.getpass("Confirm password: "):
            sys.exit("Passwords don't match.")

        db = DatabaseManager(db_path=str(DB_PATH))
        asyncio.run(db.init_db())
        username = asyncio.run(reset_password(db, password, args.username))
        print(f"Password updated for '{username}'.")

    elif args.command == "clean-documents":
        db = DatabaseManager(db_path=str(DB_PATH))
        asyncio.run(db.init_db())
        orphans = asyncio.run(clean_documents(db, delete=args.delete))
        for path in orphans:
            print(path)
        if not orphans:
            print("Every document belongs to a load; nothing to clean.")
        elif args.delete:
            print(f"Deleted {len(orphans)} file(s) no load linked to.")
        else:
            print(f"{len(orphans)} file(s) no load links to. Run again with --delete to remove them.")


if __name__ == "__main__":
    main()
