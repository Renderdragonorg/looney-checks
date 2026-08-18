"""HTTP API tests using the standard library only."""

from __future__ import annotations

import json
import os
import tempfile
import time
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from unittest.mock import patch

from music_copyright_checker.server import _validate_remote_url, create_server


class _FakeResult:
    def to_dict(self):
        return {"request": {"source": "spotify"}, "research": {"status": "complete"}, "ai_meta": {}}


class _FakePipeline:
    def check_spotify_url(self, value, *, progress=None):
        self.last_value = value
        if progress:
            progress("researching", "Fake research complete.")
        return _FakeResult()

    def check_file(self, value, *, progress=None, fallback_title=None):
        self.last_value = value
        self.fallback_title = fallback_title
        if progress:
            progress("extracting_metadata", "Fake metadata extracted.")
        return _FakeResult()


class TestServer(unittest.TestCase):
    def setUp(self):
        self.pipeline = _FakePipeline()
        self.server = create_server("127.0.0.1", 0, self.pipeline, model="opencode/big-pickle")
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base_url = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)


class TestServerWithJobs(TestServer):
    def setUp(self):
        self.pipeline = _FakePipeline()
        self.server = create_server(
            "127.0.0.1", 0, self.pipeline, model="opencode/big-pickle", jobs_enabled=True
        )
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base_url = f"http://127.0.0.1:{self.server.server_port}"

    def test_health(self):
        with urlopen(f"{self.base_url}/health") as response:
            self.assertEqual(response.status, 200)
            self.assertEqual(json.load(response)["ai_model"], "opencode/big-pickle")

    def test_docs_describes_check_endpoint(self):
        with urlopen(f"{self.base_url}/docs") as response:
            payload = json.load(response)
        check = next(item for item in payload["endpoints"] if item["path"] == "/check")
        self.assertEqual(check["method"], "POST")
        self.assertIn("one_of", check["request"])
        self.assertIn("usage_assessment", check["response"]["research"])
        self.assertIn("social_media_verdict", check["response"]["research"]["usage_assessment"])
        self.assertEqual(payload["implementation_steps"][0]["title"], "Install")
        self.assertIn("client_file_upload", payload["request_modes"])
        self.assertIn("research.sources", payload["response_fields"])

    def test_docs_with_jobs_describes_job_endpoints(self):
        from music_copyright_checker.server import _docs_payload

        payload = _docs_payload("opencode/big-pickle", jobs_enabled=True)
        self.assertTrue(any(item["path"].endswith("/events") for item in payload["endpoints"]))
        self.assertIn("/jobs", payload["proxy_note"])

    def test_check_routes_spotify_request(self):
        request = Request(
            f"{self.base_url}/check",
            data=json.dumps({"spotify_url": "spotify:track:abc"}).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(request) as response:
            payload = json.load(response)
        self.assertEqual(payload["research"]["status"], "complete")
        self.assertEqual(self.pipeline.last_value, "spotify:track:abc")

    def test_check_routes_multipart_file_and_cleans_up(self):
        boundary = "----music-checker-test"
        body = (
            f"--{boundary}\r\n"
            'Content-Disposition: form-data; name="file"; filename="sample.mp3"\r\n'
            "Content-Type: audio/mpeg\r\n\r\n"
            "not-real-audio\r\n"
            f"--{boundary}--\r\n"
        ).encode()
        request = Request(
            f"{self.base_url}/check",
            data=body,
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
            method="POST",
        )
        with urlopen(request) as response:
            payload = json.load(response)
        self.assertEqual(payload["research"]["status"], "complete")
        self.assertTrue(self.pipeline.last_value.endswith(".mp3"))
        self.assertEqual(self.pipeline.fallback_title, "sample")
        self.assertFalse(os.path.exists(self.pipeline.last_value))

    def test_jobs_queue_and_return_result(self):
        request = Request(
            f"{self.base_url}/jobs",
            data=json.dumps({"spotify_url": "spotify:track:abc"}).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(request) as response:
            queued = json.load(response)
        self.assertEqual(queued["status"], "queued")

        result = None
        for _ in range(20):
            with urlopen(f"{self.base_url}{queued['status_url']}") as response:
                result = json.load(response)
            if result["status"] == "complete":
                break
            time.sleep(0.01)
        self.assertIsNotNone(result)
        self.assertEqual(result["status"], "complete")
        self.assertEqual(result["result"]["research"]["status"], "complete")

    @patch("music_copyright_checker.server._download_remote_file")
    def test_jobs_route_direct_file_url(self, download):
        temporary = tempfile.NamedTemporaryFile(suffix=".mp3", delete=False)
        temporary.close()
        download.return_value = (temporary.name, "Remote Song")
        request = Request(
            f"{self.base_url}/jobs",
            data=json.dumps({"file_url": "https://cdn.example.com/remote.mp3"}).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(request) as response:
            queued = json.load(response)
        for _ in range(20):
            with urlopen(f"{self.base_url}{queued['status_url']}") as response:
                result = json.load(response)
            if result["status"] == "complete":
                break
            time.sleep(0.01)
        self.assertEqual(result["status"], "complete")
        self.assertEqual(self.pipeline.fallback_title, "Remote Song")
        self.assertFalse(os.path.exists(temporary.name))

    def test_remote_url_rejects_private_addresses(self):
        with self.assertRaises(ValueError):
            _validate_remote_url("http://127.0.0.1:8090/health")

    def test_job_events_stream_progress(self):
        request = Request(
            f"{self.base_url}/jobs",
            data=json.dumps({"spotify_url": "spotify:track:abc"}).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(request) as response:
            queued = json.load(response)
        with urlopen(f"{self.base_url}{queued['status_url']}/events") as response:
            stream = response.read().decode()
        self.assertIn("event: progress", stream)
        self.assertIn('"stage": "complete"', stream)

    def test_check_rejects_multiple_sources(self):
        request = Request(
            f"{self.base_url}/check",
            data=json.dumps({"spotify_url": "x", "file": "song.mp3"}).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with self.assertRaises(HTTPError) as error:
            urlopen(request)
        self.assertEqual(error.exception.code, 400)


if __name__ == "__main__":
    unittest.main()
