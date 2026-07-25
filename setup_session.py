"""One-time helper: import your Instagram session from Chrome into an instaloader
session file, so the bot can access stories/highlights/profile pics WITHOUT a
password login (which Instagram blocks from servers).

USAGE (run it yourself):
    python setup_session.py

Requirements:
    - You are logged into instagram.com in Chrome on THIS computer.
    - browser_cookie3 is installed (already done).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

USERNAME = os.getenv("IG_USERNAME", "").strip()
SESSION_DIR = Path(os.getenv("SESSION_DIR", ".sessions"))


def main() -> int:
    import instaloader

    L = instaloader.Instaloader(max_connection_attempts=1, quiet=True)

    # 1) Try to pull cookies straight from Chrome.
    try:
        import browser_cookie3

        cookies = browser_cookie3.chrome(domain_name=".instagram.com")
        L.context._session.cookies.update(cookies)
    except Exception as exc:  # decryption / no cookies / browser locked
        print(f"[!] Could not read Chrome cookies automatically: {exc}")
        # 2) Fallback: manual sessionid from the environment.
        sid = os.getenv("IG_SESSIONID", "").strip()
        if not sid:
            print(
                "\nFallback: set IG_SESSIONID in your .env with the 'sessionid' cookie\n"
                "value from Chrome DevTools (F12 > Application > Cookies > instagram.com),\n"
                "then run this script again."
            )
            return 2
        L.context._session.cookies.set("sessionid", sid, domain=".instagram.com")

    # 3) Verify the session actually works.
    try:
        username = L.test_login()
    except Exception as exc:
        print(f"[x] Login test failed: {exc}")
        return 1

    if not username:
        print(
            "[x] Not logged in. Open instagram.com in Chrome, sign in as your bot "
            "account, then run this script again."
        )
        return 1

    L.context.username = username
    SESSION_DIR.mkdir(parents=True, exist_ok=True)
    # Save under the .env username if given, else the detected one.
    session_file = SESSION_DIR / f"session-{USERNAME or username}"
    L.save_session_to_file(str(session_file))

    print(f"[OK] Session imported for @{username}")
    print(f"[OK] Saved to: {session_file}")
    if USERNAME and USERNAME != username:
        print(
            f"[!] Note: .env IG_USERNAME is '{USERNAME}' but you are logged in as "
            f"'{username}'. Update IG_USERNAME to '{username}' so the bot finds this session."
        )
    print("\nNow restart the bot — stories/highlights/profile pics will work.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
