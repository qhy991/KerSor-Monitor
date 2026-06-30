#!/usr/bin/env python3
"""Small OTLP HTTP receiver for KDA worker telemetry.

This receiver intentionally stores raw OTLP requests on disk instead of trying
to become a full OpenTelemetry collector. The local plugin can pull and
summarize those run directories later.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import signal
import threading
import zlib
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


OTLP_PATHS = {"/v1/logs", "/v1/metrics", "/v1/traces"}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def sanitize_path(path: str) -> str:
    text = path.strip("/") or "root"
    return "".join(char if char.isalnum() or char in "._-" else "_" for char in text)


def printable_text(data: bytes) -> str:
    return data.decode("utf-8", errors="replace").translate(
        {codepoint: "." for codepoint in range(32) if codepoint not in (9, 10, 13)}
    )


def decode_body(raw_body: bytes, content_encoding: str | None) -> tuple[bytes | None, str | None]:
    encoding = (content_encoding or "").strip().lower()
    if not encoding or encoding == "identity":
        return raw_body, None
    try:
        if encoding == "gzip":
            return gzip.decompress(raw_body), None
        if encoding == "deflate":
            return zlib.decompress(raw_body), None
    except Exception as exc:  # pragma: no cover - exact zlib errors vary.
        return None, f"{type(exc).__name__}: {exc}"
    return None, f"unsupported content-encoding: {encoding}"


class RequestStore:
    def __init__(self, output_dir: Path) -> None:
        self.output_dir = output_dir
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.index_path = self.output_dir / "index.ndjson"
        self._lock = threading.Lock()
        self._sequence = 0

    def write_request(
        self,
        *,
        method: str,
        request_path: str,
        query: str,
        headers: dict[str, str],
        raw_body: bytes,
        status: int,
    ) -> dict[str, Any]:
        with self._lock:
            self._sequence += 1
            sequence = self._sequence

        prefix = f"{sequence:04d}-{sanitize_path(request_path)}"
        raw_path = self.output_dir / f"{prefix}.body.bin"
        text_path = self.output_dir / f"{prefix}.body.txt"
        meta_path = self.output_dir / f"{prefix}.meta.json"

        raw_path.write_bytes(raw_body)
        content_type = headers.get("content-type", "")
        decoded_body, decode_error = decode_body(raw_body, headers.get("content-encoding"))
        text_preview_file = None
        if decoded_body is not None and any(token in content_type for token in ("json", "text", "protobuf", "octet-stream")):
            text_path.write_text(printable_text(decoded_body), encoding="utf-8")
            text_preview_file = str(text_path)

        record = {
            "sequence": sequence,
            "timestamp": utc_now(),
            "method": method,
            "path": request_path,
            "query": query,
            "status": status,
            "content_type": content_type,
            "content_encoding": headers.get("content-encoding") or None,
            "raw_bytes": len(raw_body),
            "sha256": hashlib.sha256(raw_body).hexdigest(),
            "raw_body_file": str(raw_path),
            "text_preview_file": text_preview_file,
            "meta_file": str(meta_path),
            "decode_error": decode_error,
            "headers": headers,
        }

        meta_path.write_text(json.dumps(record, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        with self._lock:
            with self.index_path.open("a", encoding="utf-8") as index_file:
                index_file.write(json.dumps(record, ensure_ascii=False) + "\n")
        return record


class OTelRequestHandler(BaseHTTPRequestHandler):
    server_version = "KDAOtelReceiver/1.0"

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API.
        parsed = urlparse(self.path)
        status = 200 if parsed.path in OTLP_PATHS else 404
        length = int(self.headers.get("content-length") or "0")
        raw_body = self.rfile.read(length)
        headers = {key.lower(): value for key, value in self.headers.items()}

        store: RequestStore = self.server.request_store  # type: ignore[attr-defined]
        record = store.write_request(
            method=self.command,
            request_path=parsed.path,
            query=parsed.query,
            headers=headers,
            raw_body=raw_body,
            status=status,
        )
        if not getattr(self.server, "quiet", False):  # type: ignore[attr-defined]
            print(
                json.dumps(
                    {
                        "sequence": record["sequence"],
                        "path": record["path"],
                        "raw_bytes": record["raw_bytes"],
                        "content_type": record["content_type"],
                        "status": status,
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )

        self.send_response(status)
        self.send_header("content-type", "application/json")
        self.end_headers()
        self.wfile.write(b"{}")

    def log_message(self, format: str, *args: Any) -> None:
        return


def make_server(host: str, port: int, output_dir: Path, quiet: bool = False) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer((host, port), OTelRequestHandler)
    server.request_store = RequestStore(output_dir)  # type: ignore[attr-defined]
    server.quiet = quiet  # type: ignore[attr-defined]
    return server


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Capture OTLP HTTP requests for KDA worker telemetry.")
    parser.add_argument("--host", default="127.0.0.1", help="Bind host. Default: 127.0.0.1")
    parser.add_argument("--port", type=int, default=4318, help="Bind port. Default: 4318")
    parser.add_argument("--output-dir", required=True, help="Directory for index/meta/body files.")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    output_dir = Path(args.output_dir).resolve()
    server = make_server(args.host, args.port, output_dir)

    def stop_server(signum: int, frame: Any) -> None:
        del signum, frame
        server.shutdown()

    signal.signal(signal.SIGTERM, stop_server)
    signal.signal(signal.SIGINT, stop_server)
    print(
        json.dumps(
            {
                "listening": f"http://{args.host}:{args.port}",
                "output_dir": str(output_dir),
                "index": str(output_dir / "index.ndjson"),
            },
            ensure_ascii=False,
        ),
        flush=True,
    )
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
