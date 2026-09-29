"""Loopback Laya fixture used only by browser integration checks."""

import argparse
from http.server import ThreadingHTTPServer

from check_laya import FakeLaya


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, required=True)
    args = parser.parse_args()
    ThreadingHTTPServer(("127.0.0.1", args.port), FakeLaya).serve_forever()
