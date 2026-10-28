"""Check what the parser extracts from a real vacancy page, without starting the app.

python -m scripts.try_parse https://www.startupjobs.cz/nabidka/12345/...
"""

import sys
from dataclasses import asdict

from app.services.job_parser import parse_job


def main() -> None:
    if len(sys.argv) != 2:
        sys.exit("usage: python -m scripts.try_parse <url>")
    job = parse_job(sys.argv[1])
    for key, value in asdict(job).items():
        if key == "description" and value:
            value = value[:300].replace("\n", " ") + ("…" if len(value) > 300 else "")
        print(f"{key:>22}: {value}")
    print(f"{'needs_manual':>22}: {job.needs_manual}")


if __name__ == "__main__":
    main()
