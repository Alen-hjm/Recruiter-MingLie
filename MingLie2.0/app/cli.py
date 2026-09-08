from __future__ import annotations

import argparse
from app.factory import create_app


def main():
    parser=argparse.ArgumentParser(description="MingLie 2.0 maintenance commands")
    parser.add_argument("command", choices=("check",))
    args=parser.parse_args(); app=create_app()
    if args.command == "check":
        with app.test_client() as client:
            response=client.get("/api/v1/health")
            assert response.status_code == 200
        print("MingLie 2.0 self-check passed")

if __name__ == "__main__": main()
