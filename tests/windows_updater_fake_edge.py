"""Tiny TCP listener packaged as msedge.exe for updater ownership E2E."""
from __future__ import annotations

import argparse
import socket


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, required=True)
    args = parser.parse_args()

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("127.0.0.1", int(args.port)))
    sock.listen(8)
    sock.settimeout(1.0)
    try:
        while True:
            try:
                conn, _ = sock.accept()
            except socket.timeout:
                continue
            try:
                conn.close()
            except OSError:
                pass
    finally:
        sock.close()


if __name__ == "__main__":
    raise SystemExit(main())
