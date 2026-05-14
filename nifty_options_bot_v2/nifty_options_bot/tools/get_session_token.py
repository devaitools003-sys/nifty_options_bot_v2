"""
get_session_token.py
====================
Run this ONCE every morning before starting the bot.

It opens your browser to the ICICIdirect login page, you log in,
and it prints the session token to paste into config/config.yml.

Usage
-----
    python tools/get_session_token.py

Steps
-----
1. Run this script
2. Your browser opens the ICICI login URL
3. Log in with your ICICI Direct credentials
4. After login you are redirected to a URL like:
       https://api.icicidirect.com/?apisession=XXXXXXXXXX
5. Copy the value after  ?apisession=
6. Paste it as session_token in config/config.yml
7. Start the bot:  python main.py
"""

import webbrowser
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.config_loader import get_config

def main():
    print("=" * 60)
    print("  Nifty Options Bot — Daily Session Token Helper")
    print("=" * 60)

    cfg = get_config()
    api_key = cfg.broker.get("api_key", "")

    if not api_key or api_key == "YOUR_BREEZE_API_KEY":
        print("\n❌  Please set your api_key in config/config.yml first.")
        sys.exit(1)

    login_url = f"https://api.icicidirect.com/apiuser/login?api_key={api_key}"

    print(f"\n📌  Opening browser for ICICI Direct login...")
    print(f"    URL: {login_url}\n")
    webbrowser.open(login_url)

    print("After logging in, you will be redirected to a URL like:")
    print("   https://api.icicidirect.com/?apisession=XXXXXXXXXX\n")

    token = input("Paste the session token (value after ?apisession=): ").strip()

    if not token:
        print("❌  No token entered. Exiting.")
        sys.exit(1)

    # Update config file
    config_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "config", "config.yml"
    )
    with open(config_path, "r") as f:
        content = f.read()

    # Replace session_token line
    import re
    updated = re.sub(
        r'session_token:.*',
        f'session_token: "{token}"',
        content
    )
    with open(config_path, "w") as f:
        f.write(updated)

    print(f"\n✅  Session token saved to config/config.yml")
    print(f"    Token: {token[:8]}...{token[-4:]}")
    print(f"\n🚀  You can now run: python main.py")
    print("=" * 60)

if __name__ == "__main__":
    main()
