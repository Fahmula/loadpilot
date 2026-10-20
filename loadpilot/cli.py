"""Admin commands for when you can't log in.

In Docker:  docker exec -it loadpilot python -m loadpilot.cli reset-password
"""

import argparse
import asyncio
import getpass
import sys

from loadpilot.auth import MIN_PASSWORD_LENGTH, hash_password
from loadpilot.config import DB_PATH
from loadpilot.database import DatabaseManager


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


def main(argv: list[str] | None = None):
    parser = argparse.ArgumentParser(prog="python -m loadpilot.cli")
    commands = parser.add_subparsers(dest="command", required=True)
    reset = commands.add_parser("reset-password", help="Set a new login password (signs out all devices)")
    reset.add_argument("--username", help="Account to reset (only needed if there are several)")
    args = parser.parse_args(argv)

    if args.command == "reset-password":
        password = getpass.getpass("New password: ")
        if password != getpass.getpass("Confirm password: "):
            sys.exit("Passwords don't match.")

        db = DatabaseManager(db_path=str(DB_PATH))
        asyncio.run(db.init_db())
        username = asyncio.run(reset_password(db, password, args.username))
        print(f"Password updated for '{username}'.")


if __name__ == "__main__":
    main()
