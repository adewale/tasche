"""Post-deploy smoke test — verifies Worker is alive and responding."""

import json
import sys
import time
import urllib.request

MAX_RETRIES = 3
RETRY_DELAY = 3  # seconds — Python Worker cold start


def check(base, path, validate):
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            req = urllib.request.Request(
                base + path, headers={"User-Agent": "Tasche-Smoke/1.0"}
            )
            resp = urllib.request.urlopen(req, timeout=10)
            data = json.loads(resp.read())
            if validate(data):
                return True
            print(f"  WARN: {path} returned unexpected data (attempt {attempt})")
        except Exception as e:
            print(f"  WARN: {path} failed (attempt {attempt}): {e}")
        if attempt < MAX_RETRIES:
            time.sleep(RETRY_DELAY)
    return False


CHECKS = [
    ("/api/health", lambda d: d.get("status") == "ok"),
    ("/api/health/config", lambda d: d.get("status") in ("ok", "degraded", "error")),
]


def main(base=None):
    if base is None:
        base = (
            sys.argv[1]
            if len(sys.argv) > 1
            else "https://tasche-staging.adewale-883.workers.dev"
        )

    print(f"Smoke testing {base}...")
    failed = [path for path, v in CHECKS if not check(base, path, v)]
    if failed:
        print(f"FAILED: {', '.join(failed)}")
        sys.exit(1)
    print("All smoke checks passed")


if __name__ == "__main__":
    main()
