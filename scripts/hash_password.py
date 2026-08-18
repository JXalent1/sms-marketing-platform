#!/usr/bin/env python3
"""Generate a bcrypt hash for ADMIN_PASSWORD_HASH.

    python scripts/hash_password.py

Paste the output into .env. Keeping the plaintext out of .env means a leaked
config file doesn't hand over the dashboard, and the password never lands in
shell history or a screenshot.
"""

import getpass
from passlib.context import CryptContext

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

if __name__ == "__main__":
    password = getpass.getpass("New admin password: ")
    confirm = getpass.getpass("Confirm: ")

    if password != confirm:
        raise SystemExit("Passwords do not match.")
    if len(password) < 12:
        raise SystemExit("Use at least 12 characters — this dashboard can spend money.")

    print("\nAdd this line to your .env:\n")
    print(f"ADMIN_PASSWORD_HASH={pwd_context.hash(password)}")
