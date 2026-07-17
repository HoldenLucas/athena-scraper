import os
import subprocess
import sys

baseUrl = "https://docs.athenahealth.com/api/docs/"

# Headed only when HEADED=1 (local debugging); CI/default stays headless
headless = os.environ.get("HEADED") != "1"


def launchChromium(p):
    # ponytail: install-on-first-run, retries once after fetching the browser
    try:
        return p.chromium.launch(headless=headless)
    except Exception:
        subprocess.run(
            [sys.executable, "-m", "playwright", "install", "chromium"], check=True
        )
        return p.chromium.launch(headless=headless)
