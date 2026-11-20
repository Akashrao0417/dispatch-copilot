"""Set a user's password for AUTH_MODE=password.

  python -m backend.set_password manager
  python -m backend.set_password t-001
"""
import getpass
import sys

from backend import db
from backend.auth import set_password


def main() -> None:
    if len(sys.argv) != 2:
        sys.exit("usage: python -m backend.set_password <username>")
    username = sys.argv[1].strip().lower()
    pw = getpass.getpass(f"New password for {username}: ")
    if pw != getpass.getpass("Repeat: "):
        sys.exit("passwords don't match")
    with db.connect() as conn:
        db.init_app_tables(conn)
        set_password(conn, username, pw)
    print(f"password set for {username}")


if __name__ == "__main__":
    main()
