from __future__ import annotations

import json
import socket
import sys
import tempfile
import threading
import unittest
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import otel_receiver  # noqa: E402


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


class OTelReceiverTests(unittest.TestCase):
    def test_receiver_writes_index_meta_raw_and_text(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            output_dir = Path(tempdir)
            port = free_port()
            server = otel_receiver.make_server("127.0.0.1", port, output_dir, quiet=True)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            self.addCleanup(server.server_close)

            payload = {"resourceLogs": [{"scopeLogs": [{"logRecords": [{"body": {"stringValue": "ok"}}]}]}]}
            request = urllib.request.Request(
                f"http://127.0.0.1:{port}/v1/logs",
                data=json.dumps(payload).encode("utf-8"),
                headers={"content-type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(request, timeout=5) as response:
                self.assertEqual(response.status, 200)
                response.read()
            server.shutdown()
            thread.join(timeout=5)

            index_rows = [json.loads(line) for line in (output_dir / "index.ndjson").read_text().splitlines()]
            self.assertEqual(len(index_rows), 1)
            self.assertEqual(index_rows[0]["path"], "/v1/logs")
            self.assertTrue(Path(index_rows[0]["raw_body_file"]).exists())
            self.assertTrue(Path(index_rows[0]["meta_file"]).exists())
            self.assertIn("resourceLogs", Path(index_rows[0]["text_preview_file"]).read_text())


if __name__ == "__main__":
    unittest.main()
