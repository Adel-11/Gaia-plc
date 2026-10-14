"""Container health probe using only Python's standard library."""
import os
from urllib.request import urlopen


def main():
    port = int(os.environ.get("PORT", "8000"))
    with urlopen(f"http://127.0.0.1:{port}/api/health", timeout=3) as response:
        if response.status != 200:
            raise SystemExit(1)


if __name__ == "__main__":
    main()
