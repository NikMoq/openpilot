#!/usr/bin/env python3
"""
Simple HTTP server for mapd-russia tiles.
Supports Range requests for resumable downloads.

Usage:
    python serve.py --root ./tiles --port 8080
"""

import argparse
import mimetypes
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path


class TileHandler(BaseHTTPRequestHandler):
    root: Path = Path(".")

    def log_message(self, fmt, *args):
        print(f"[{self.address_string()}] {fmt % args}")

    def do_GET(self):
        path = self.path.lstrip("/")
        file_path = self.root / path

        # Security: prevent directory traversal
        try:
            file_path.relative_to(self.root)
        except ValueError:
            self.send_error(403, "Forbidden")
            return

        if not file_path.exists() or file_path.is_dir():
            self.send_error(404, "Not Found")
            return

        # Range request support
        file_size = file_path.stat().st_size
        start = 0
        end = file_size - 1

        range_header = self.headers.get("Range")
        if range_header:
            try:
                range_val = range_header.replace("bytes=", "")
                if "-" in range_val:
                    parts = range_val.split("-")
                    if parts[0]:
                        start = int(parts[0])
                    if parts[1]:
                        end = int(parts[1])
            except (ValueError, IndexError):
                pass

        content_length = end - start + 1

        if range_header:
            self.send_response(206)
            self.send_header("Content-Range", f"bytes {start}-{end}/{file_size}")
        else:
            self.send_response(200)

        mime, _ = mimetypes.guess_type(str(file_path))
        self.send_header("Content-Type", mime or "application/octet-stream")
        self.send_header("Content-Length", str(content_length))
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Cache-Control", "public, max-age=3600")
        self.end_headers()

        with open(file_path, "rb") as f:
            f.seek(start)
            remaining = content_length
            while remaining > 0:
                chunk = f.read(min(65536, remaining))
                if not chunk:
                    break
                self.wfile.write(chunk)
                remaining -= len(chunk)

    def do_HEAD(self):
        path = self.path.lstrip("/")
        file_path = self.root / path
        try:
            file_path.relative_to(self.root)
        except ValueError:
            self.send_error(403)
            return
        if not file_path.exists():
            self.send_error(404)
            return

        self.send_response(200)
        mime, _ = mimetypes.guess_type(str(file_path))
        self.send_header("Content-Type", mime or "application/octet-stream")
        self.send_header("Content-Length", str(file_path.stat().st_size))
        self.send_header("Accept-Ranges", "bytes")
        self.end_headers()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path("./tiles"), help="Tile root directory")
    parser.add_argument("--port", type=int, default=8080, help="HTTP port")
    parser.add_argument("--host", default="0.0.0.0", help="HTTP host")
    args = parser.parse_args()

    TileHandler.root = args.root.resolve()
    server = HTTPServer((args.host, args.port), TileHandler)
    print(f"Serving {TileHandler.root} at http://{args.host}:{args.port}")
    print("Press Ctrl+C to stop")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down...")
        server.shutdown()


if __name__ == "__main__":
    main()
