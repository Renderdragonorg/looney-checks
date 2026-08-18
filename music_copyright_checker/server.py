"""Small JSON HTTP server that routes requests through :class:`Pipeline`."""

from __future__ import annotations

import argparse
import ipaddress
import json
import os
import socket
import sys
import tempfile
import threading
import time
import uuid
from email.parser import BytesParser
from email.policy import default as email_policy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import PurePath
from typing import Any, Callable, Dict, Optional, Type
from urllib.parse import unquote, urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .ai_researcher import DEFAULT_OPENCODE_MODEL, DEFAULT_OPENCODE_TIMEOUT
from .errors import MusicCheckerError
from .pipeline import Pipeline

MAX_JSON_BODY_BYTES = 1_000_000
MAX_UPLOAD_BYTES = 100 * 1024 * 1024
REMOTE_DOWNLOAD_TIMEOUT_SECONDS = 60.0
REMOTE_CONTENT_TYPES = {
    "audio/aac": ".aac",
    "audio/flac": ".flac",
    "audio/m4a": ".m4a",
    "audio/mp4": ".m4a",
    "audio/mpeg": ".mp3",
    "audio/ogg": ".ogg",
    "audio/opus": ".opus",
    "audio/wav": ".wav",
    "audio/x-wav": ".wav",
}


def _save_multipart_file(content_type: str, body: bytes) -> tuple[str, str]:
    """Extract the ``file`` form part into a temporary file and return its path."""
    header = f"Content-Type: {content_type}\r\nMIME-Version: 1.0\r\n\r\n".encode("ascii")
    message = BytesParser(policy=email_policy).parsebytes(header + body)
    if not message.is_multipart():
        raise ValueError("Request must be multipart/form-data with a file field.")

    for part in message.iter_parts():
        if part.get_param("name", header="Content-Disposition") != "file":
            continue
        filename = part.get_filename()
        if not filename:
            raise ValueError("The uploaded file must include a filename.")
        suffix = PurePath(filename).suffix.lower()
        if not suffix:
            raise ValueError("The uploaded file must have an audio extension.")
        data = part.get_payload(decode=True)
        if not isinstance(data, bytes):
            raise ValueError("Could not read the uploaded file contents.")

        temporary = tempfile.NamedTemporaryFile(prefix="music-copyright-", suffix=suffix, delete=False)
        try:
            temporary.write(data)
        finally:
            temporary.close()
        return temporary.name, PurePath(filename).stem

    raise ValueError("Multipart request must include a file field named 'file'.")


def _validate_remote_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("file_url must be an absolute HTTP or HTTPS URL.")
    if parsed.username or parsed.password:
        raise ValueError("file_url must not contain embedded credentials.")
    try:
        addresses = {info[4][0] for info in socket.getaddrinfo(parsed.hostname, parsed.port or 443)}
    except (socket.gaierror, ValueError) as exc:
        raise ValueError("file_url hostname could not be resolved.") from exc
    if not addresses or any(not ipaddress.ip_address(address).is_global for address in addresses):
        raise ValueError("file_url must resolve to a public internet address.")


class _SafeRedirectHandler(HTTPRedirectHandler):
    def redirect_request(self, request: Request, fp: Any, code: int, msg: str, headers: Any, newurl: str) -> Optional[Request]:
        _validate_remote_url(newurl)
        return super().redirect_request(request, fp, code, msg, headers, newurl)


def _download_remote_file(url: str) -> tuple[str, str]:
    """Download a public audio URL into a temporary file and return path/title hint."""
    _validate_remote_url(url)
    opener = build_opener(_SafeRedirectHandler())
    request = Request(url, headers={"User-Agent": "music-copyright-checker/0.1"})
    try:
        response = opener.open(request, timeout=REMOTE_DOWNLOAD_TIMEOUT_SECONDS)
    except Exception as exc:
        raise ValueError(f"Could not download file_url: {exc}") from exc

    with response:
        final_url = response.geturl()
        _validate_remote_url(final_url)
        filename = response.headers.get_filename() or PurePath(unquote(urlparse(final_url).path)).name
        content_type = response.headers.get_content_type().lower()
        suffix = PurePath(filename).suffix.lower()
        if not suffix:
            suffix = REMOTE_CONTENT_TYPES.get(content_type, "")
        if not suffix:
            raise ValueError("file_url must identify an audio file with a supported extension or audio Content-Type.")
        title_hint = PurePath(filename).stem or "downloaded audio"

        temporary = tempfile.NamedTemporaryFile(prefix="music-copyright-", suffix=suffix, delete=False)
        total = 0
        try:
            while True:
                chunk = response.read(64 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > MAX_UPLOAD_BYTES:
                    raise ValueError("Downloaded file exceeds the 100 MB limit.")
                temporary.write(chunk)
        except Exception:
            try:
                os.unlink(temporary.name)
            except FileNotFoundError:
                pass
            raise
        finally:
            temporary.close()
        return temporary.name, title_hint


class JobStore:
    """In-memory background jobs for requests that exceed proxy timeouts."""

    def __init__(self) -> None:
        self._jobs: Dict[str, Dict[str, Any]] = {}
        self._lock = threading.Lock()

    def submit(self, work: Callable[[Callable[[str, str], None]], Dict[str, Any]]) -> str:
        job_id = uuid.uuid4().hex
        with self._lock:
            self._jobs[job_id] = {
                "job_id": job_id,
                "status": "queued",
                "progress": {"stage": "queued", "message": "Waiting to start."},
                "events": [{"id": 0, "stage": "queued", "message": "Waiting to start."}],
            }
        threading.Thread(target=self._run, args=(job_id, work), daemon=True).start()
        return job_id

    def _record(self, job_id: str, stage: str, message: str) -> None:
        with self._lock:
            job = self._jobs[job_id]
            event = {"id": len(job["events"]), "stage": stage, "message": message}
            job["progress"] = {"stage": stage, "message": message}
            job["events"].append(event)

    def _run(self, job_id: str, work: Callable[[Callable[[str, str], None]], Dict[str, Any]]) -> None:
        with self._lock:
            self._jobs[job_id]["status"] = "running"
        self._record(job_id, "running", "Starting the copyright check.")

        def progress(stage: str, message: str) -> None:
            self._record(job_id, stage, message)

        try:
            outcome = {"status": "complete", "result": work(progress)}
        except MusicCheckerError as exc:
            outcome = {"status": "failed", "error": str(exc)}
            self._record(job_id, "failed", str(exc))
        except ValueError as exc:
            outcome = {"status": "failed", "error": str(exc)}
            self._record(job_id, "failed", str(exc))
        except Exception:
            outcome = {"status": "failed", "error": "Unexpected server error while checking the track."}
            self._record(job_id, "failed", outcome["error"])
        with self._lock:
            self._jobs[job_id].update(outcome)
        if outcome["status"] == "complete":
            self._record(job_id, "complete", "Copyright and licensing research finished.")

    def get(self, job_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                return None
            result = dict(job)
            result["events"] = list(job["events"])
            result["progress"] = dict(job["progress"])
            return result

    def events_since(self, job_id: str, after: int) -> Optional[tuple[Dict[str, Any], list[Dict[str, Any]]]]:
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                return None
            return job, list(job["events"][after + 1 :])


def _docs_payload(model: Optional[str], *, jobs_enabled: bool = False) -> Dict[str, Any]:
    """Return the integration contract exposed by the running server."""
    endpoints: list[Dict[str, Any]] = [
        {
            "method": "GET",
            "path": "/health",
            "description": "Readiness check.",
            "response": {"status": "ok", "service": "music-copyright-checker", "ai_model": model},
        },
        {
            "method": "GET",
            "path": "/docs",
            "description": "This machine-readable API documentation.",
        },
        {
            "method": "POST",
            "path": "/check",
            "description": "Run the AI copyright and licensing research pipeline.",
            "request": {
                "one_of": [
                    {"spotify_url": "https://open.spotify.com/track/..."},
                    {"file": "/absolute/path/to/song.mp3"},
                    {"file_url": "https://cdn.example.com/song.mp3"},
                    {"multipart_file": "file=@song.mp3"},
                    {"spotify_url": "https://open.spotify.com/track/...", "refresh": True},
                ]
            },
            "response": {
                "request": "normalized track metadata and credits",
                "research": {
                    "status": "complete | partial | not_found",
                    "summary": "concise finding",
                    "matches": "rights and licensing findings",
                    "sources": "source name, exact URL, type, and supported claim",
                    "usage_assessment": {
                        "video_verdict": "online-video clearance guidance",
                        "social_media_verdict": "YouTube/TikTok/Instagram/Facebook guidance",
                        "reality_tv_verdict": "reality-TV clearance guidance",
                        "sync_license_required": "composition clearance flag",
                        "master_license_required": "recording clearance flag",
                        "platform_exception": "platform-library and Content ID distinction",
                        "caveats": "short practical caveats",
                    },
                    "official_licensing_contacts": "verified contact URLs",
                    "warnings": "uncertainties and caveats",
                },
                "ai_meta": "model, cost, tokens, session, and cache metadata",
            },
            "errors": {
                "400": "Invalid JSON/multipart data or both/neither source fields supplied.",
                "422": "The pipeline could not process the supplied source.",
                "500": "Unexpected server error.",
            },
        },
    ]
    if jobs_enabled:
        endpoints += [
            {
                "method": "POST",
                "path": "/jobs",
                "description": "Queue a long-running AI check and return immediately.",
                "request": "Same JSON or multipart request as POST /check.",
                "response": {"status": "queued", "job_id": "...", "status_url": "/jobs/..."},
            },
            {
                "method": "GET",
                "path": "/jobs/{job_id}",
                "description": "Poll a queued AI check.",
                "response": "queued | running | complete | failed; complete jobs include result.",
            },
            {
                "method": "GET",
                "path": "/jobs/{job_id}/events",
                "description": "Stream progress events with Server-Sent Events (SSE).",
                "response": "event: progress with stage and message; closes when complete or failed.",
            },
        ]
    examples = {
        "curl": "curl -X POST http://127.0.0.1:8080/check -H 'Content-Type: application/json' -d '{\"spotify_url\":\"https://open.spotify.com/track/4cOdK2wGLETKBW3PvgPWqT\"}'",
        "curl_upload": "curl -X POST http://127.0.0.1:8080/check -F 'file=@/path/to/song.mp3'",
        "javascript": "fetch('http://127.0.0.1:8080/check', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({spotify_url: url})}).then(r => r.json())",
    }
    implementation_steps: list[Dict[str, Any]] = [
        {
            "step": 1,
            "title": "Install",
            "commands": ["uv sync --extra dev", "opencode auth list", "opencode models"],
        },
        {
            "step": 2,
            "title": "Start",
            "command": "uv run music-copyright-checker-server --host 127.0.0.1 --port 8080 --model opencode/big-pickle --timeout 900",
        },
        {
            "step": 3,
            "title": "Check readiness",
            "command": "curl http://127.0.0.1:8080/health",
        },
        {
            "step": 4,
            "title": "Call the pipeline",
            "instruction": "POST exactly one source to /check for local calls, or /jobs through a Cloudflare/proxy deployment.",
        },
        {
            "step": 5,
            "title": "Handle the result",
            "instruction": "For /jobs, poll status_url until complete, then render request.track, research.sources, research.usage_assessment, and research.warnings.",
        },
    ]
    proxy_note = (
        "Use POST /jobs plus /jobs/{job_id}/events (SSE) or GET /jobs/{job_id} (polling) through Cloudflare "
        "because AI research can exceed proxy request timeouts. POST /check is synchronous for direct/local callers."
    )
    if jobs_enabled:
        examples["curl_async"] = "curl -X POST http://127.0.0.1:8080/jobs -H 'Content-Type: application/json' -d '{\"spotify_url\":\"https://open.spotify.com/track/4cOdK2wGLETKBW3PvgPWqT\"}'"
        examples["curl_file_url"] = "curl -X POST http://127.0.0.1:8080/jobs -H 'Content-Type: application/json' -d '{\"file_url\":\"https://cdn.example.com/song.mp3\"}'"
        examples["poll"] = "curl http://127.0.0.1:8080/jobs/{job_id}"
        examples["javascript_events"] = "const events = new EventSource(`/jobs/${jobId}/events`); events.addEventListener('progress', event => renderProgress(JSON.parse(event.data)));"
    else:
        proxy_note = (
            "This local server is synchronous: AI research runs inside the /check request, so it is "
            "intended for direct/local callers. The async /jobs endpoints are only enabled with --jobs "
            "for proxy or Cloudflare deployments."
        )
    return {
        "service": "music-copyright-checker",
        "model": model,
        "content_type": ["application/json", "multipart/form-data"],
        "endpoints": endpoints,
        "examples": examples,
        "implementation_steps": implementation_steps,
        "request_modes": {
            "spotify_json": {
                "content_type": "application/json",
                "body": {"spotify_url": "https://open.spotify.com/track/..."},
            },
            "server_file_json": {
                "content_type": "application/json",
                "body": {"file": "/absolute/path/on/server/song.mp3"},
            },
            "direct_file_url": {
                "content_type": "application/json",
                "body": {"file_url": "https://cdn.example.com/song.mp3"},
                "note": "HTTP/HTTPS public URL only; downloads are limited to 100 MB.",
            },
            "client_file_upload": {
                "content_type": "multipart/form-data",
                "form_field": "file",
                "curl": "curl -X POST http://127.0.0.1:8080/check -F 'file=@/path/to/song.mp3'",
            },
        },
        "file_upload_lifecycle": [
            "Receive the multipart file field.",
            "Write a temporary file preserving its audio extension.",
            "Extract tags with Mutagen and normalize them through Pipeline.check_file().",
            "Run Big Pickle research and return the structured result.",
            "Delete the temporary file before completing the request.",
        ],
        "direct_url_lifecycle": [
            "Validate that file_url is HTTP/HTTPS and resolves to a public address.",
            "Follow only validated redirects and download at most 100 MB.",
            "Derive a filename/title hint from Content-Disposition or the URL path.",
            "Extract metadata, run AI research, and delete the temporary download.",
        ],
        "response_fields": [
            "request.track",
            "request.credits",
            "research.matches",
            "research.sources",
            "research.usage_assessment",
            "research.official_licensing_contacts",
            "research.warnings",
            "ai_meta",
        ],
        "proxy_note": proxy_note,
        "caching": {
            "default": "SQLite at ~/.cache/music-copyright-checker/cache.sqlite3",
            "spotify_metadata_ttl_hours": 12,
            "file_metadata_ttl_days": 30,
            "research_ttl_days": 7,
            "refresh": "Set refresh=true in a JSON request to bypass cached metadata and research.",
            "audio_storage": "Audio bytes are never retained by the cache; uploads and downloads remain temporary.",
        },
        "deployment": {
            "systemd_service": "music-copyright-checker.service",
            "enable_command": "systemctl --user enable --now music-copyright-checker.service",
            "restart_policy": "Restart=always; RestartSec=10",
            "reboot_requirement": "User lingering must be enabled (loginctl show-user $USER -p Linger -> Linger=yes).",
        },
        "security": [
            "Bind to 127.0.0.1 for local-only clients.",
            "Protect the port with a firewall or authenticated reverse proxy when binding to 0.0.0.0.",
            "Server-local file paths are read with the service account's permissions.",
            "Keep OpenCode provider credentials on the server, never in browser requests.",
        ],
    }


class CheckerHTTPServer(ThreadingHTTPServer):
    """HTTP server carrying the configured pipeline and model metadata."""

    allow_reuse_address = True

    def __init__(self, server_address: tuple[str, int], handler: Type[BaseHTTPRequestHandler], *, pipeline: Pipeline, model: Optional[str], jobs_enabled: bool = False) -> None:
        super().__init__(server_address, handler)
        self.pipeline = pipeline
        self.model = model
        self.jobs_enabled = jobs_enabled
        self.jobs: Optional[JobStore] = JobStore() if jobs_enabled else None


class RequestHandler(BaseHTTPRequestHandler):
    """Routes health checks and JSON track-check requests."""

    server: CheckerHTTPServer
    server_version = "MusicCopyrightChecker/0.1"

    def _send_json(self, status: int, payload: Dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.end_headers()
        self.wfile.write(body)

    def _stream_job_events(self, job_id: str) -> None:
        if self.server.jobs is None:
            self._send_json(404, {"error": "Not found"})
            return
        try:
            after = int(self.headers.get("Last-Event-ID", "-1"))
        except ValueError:
            after = -1
        if self.server.jobs.events_since(job_id, after) is None:
            self._send_json(404, {"error": "Job not found"})
            return

        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache, no-transform")
        self.send_header("Connection", "close")
        self.send_header("X-Accel-Buffering", "no")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.close_connection = True

        end_time = time.monotonic() + 25
        last_keepalive = time.monotonic()
        try:
            while time.monotonic() < end_time:
                snapshot = self.server.jobs.events_since(job_id, after)
                if snapshot is None:
                    return
                job, events = snapshot
                for event in events:
                    self.wfile.write(
                        f"id: {event['id']}\nevent: progress\ndata: {json.dumps(event, ensure_ascii=False)}\n\n".encode()
                    )
                    self.wfile.flush()
                    after = event["id"]
                if job["status"] in {"complete", "failed"}:
                    return
                now = time.monotonic()
                if now - last_keepalive >= 10:
                    self.wfile.write(b": keep-alive\n\n")
                    self.wfile.flush()
                    last_keepalive = now
                time.sleep(0.25)
        except (BrokenPipeError, ConnectionResetError):
            return

    def do_OPTIONS(self) -> None:  # noqa: N802 - required by BaseHTTPRequestHandler
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.end_headers()

    def do_GET(self) -> None:  # noqa: N802 - required by BaseHTTPRequestHandler
        path = self.path.split("?", 1)[0]
        if path == "/health":
            self._send_json(
                200,
                {
                    "status": "ok",
                    "service": "music-copyright-checker",
                    "ai_model": self.server.model,
                },
            )
            return
        if path == "/docs":
            self._send_json(200, _docs_payload(self.server.model, jobs_enabled=self.server.jobs_enabled))
            return
        if path.startswith("/jobs/") or path == "/jobs":
            if not self.server.jobs_enabled:
                self._send_json(404, {"error": "Not found"})
                return
        if path.startswith("/jobs/") and path.endswith("/events"):
            self._stream_job_events(path[len("/jobs/") : -len("/events")])
            return
        if path.startswith("/jobs/"):
            job_id = path.removeprefix("/jobs/")
            job = self.server.jobs.get(job_id)
            if job is None:
                self._send_json(404, {"error": "Job not found"})
            else:
                self._send_json(200, job)
            return
        self._send_json(404, {"error": "Not found"})

    def do_POST(self) -> None:  # noqa: N802 - required by BaseHTTPRequestHandler
        path = self.path.split("?", 1)[0]
        if path not in {"/check", "/jobs"}:
            self._send_json(404, {"error": "Not found"})
            return
        if path == "/jobs" and not self.server.jobs_enabled:
            self._send_json(404, {"error": "Not found"})
            return

        content_type = self.headers.get("Content-Type", "")
        is_multipart = content_type.lower().startswith("multipart/form-data")
        max_body_bytes = MAX_UPLOAD_BYTES if is_multipart else MAX_JSON_BODY_BYTES
        try:
            content_length = int(self.headers.get("Content-Length", "-1"))
        except ValueError:
            content_length = -1
        if content_length < 0 or content_length > max_body_bytes:
            limit = "100 MB" if is_multipart else "1 MB"
            self._send_json(413 if content_length > max_body_bytes else 400, {"error": f"Request body must be valid data under {limit}."})
            return

        body = self.rfile.read(content_length)
        temporary_path: Optional[str] = None
        try:
            if is_multipart:
                temporary_path, fallback_title = _save_multipart_file(content_type, body)
                upload_path = temporary_path
                work = lambda progress: self.server.pipeline.check_file(
                    upload_path,
                    progress=progress,
                    fallback_title=fallback_title,
                ).to_dict()
            else:
                try:
                    payload = json.loads(body.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError):
                    self._send_json(400, {"error": "Request body must be valid UTF-8 JSON."})
                    return
                if not isinstance(payload, dict):
                    self._send_json(400, {"error": "Request body must be a JSON object."})
                    return

                spotify_url = payload.get("spotify_url")
                file_path = payload.get("file")
                file_url = payload.get("file_url")
                refresh = payload.get("refresh", False)
                values = (spotify_url, file_path, file_url)
                if sum(bool(value) for value in values) != 1 or not all(
                    value is None or isinstance(value, str) for value in values
                ):
                    self._send_json(400, {"error": "Provide exactly one string: 'spotify_url', 'file', or 'file_url'."})
                    return
                if not isinstance(refresh, bool):
                    self._send_json(400, {"error": "'refresh' must be a boolean when provided."})
                    return
                if spotify_url:
                    if refresh:
                        work = lambda progress: self.server.pipeline.check_spotify_url(
                            spotify_url, progress=progress, refresh=True
                        ).to_dict()
                    else:
                        work = lambda progress: self.server.pipeline.check_spotify_url(
                            spotify_url, progress=progress
                        ).to_dict()
                elif file_path:
                    if refresh:
                        work = lambda progress: self.server.pipeline.check_file(
                            file_path, progress=progress, refresh=True
                        ).to_dict()
                    else:
                        work = lambda progress: self.server.pipeline.check_file(
                            file_path, progress=progress
                        ).to_dict()
                else:
                    work = lambda progress: self._download_and_check(
                        file_url, progress, refresh=refresh
                    )
            if path == "/jobs":
                job_id = self.server.jobs.submit(self._with_cleanup(work, temporary_path))
                temporary_path = None
                self._send_json(202, {"job_id": job_id, "status": "queued", "status_url": f"/jobs/{job_id}"})
                return
            result = work(lambda _stage, _message: None)
        except MusicCheckerError as exc:
            self._send_json(422, {"error": str(exc)})
            return
        except ValueError as exc:
            self._send_json(400, {"error": str(exc)})
            return
        except Exception:
            self._send_json(500, {"error": "Unexpected server error while checking the track."})
            return
        finally:
            if temporary_path:
                try:
                    os.unlink(temporary_path)
                except FileNotFoundError:
                    pass

        self._send_json(200, result)

    def _download_and_check(
        self,
        url: str,
        progress: Callable[[str, str], None],
        *,
        refresh: bool = False,
    ) -> Dict[str, Any]:
        progress("downloading_file", "Downloading the remote audio file.")
        temporary_path, fallback_title = _download_remote_file(url)
        try:
            if refresh:
                return self.server.pipeline.check_file(
                    temporary_path,
                    progress=progress,
                    fallback_title=fallback_title,
                    refresh=True,
                ).to_dict()
            return self.server.pipeline.check_file(
                temporary_path,
                progress=progress,
                fallback_title=fallback_title,
            ).to_dict()
        finally:
            try:
                os.unlink(temporary_path)
            except FileNotFoundError:
                pass

    @staticmethod
    def _with_cleanup(
        work: Callable[[Callable[[str, str], None]], Dict[str, Any]],
        temporary_path: Optional[str],
    ) -> Callable[[Callable[[str, str], None]], Dict[str, Any]]:
        def run(progress: Callable[[str, str], None]) -> Dict[str, Any]:
            try:
                return work(progress)
            finally:
                if temporary_path:
                    try:
                        os.unlink(temporary_path)
                    except FileNotFoundError:
                        pass

        return run

    def log_message(self, format: str, *args: Any) -> None:
        print(f"{self.address_string()} - {format % args}", file=sys.stderr)


def create_server(host: str, port: int, pipeline: Pipeline, *, model: Optional[str] = None, jobs_enabled: bool = False) -> CheckerHTTPServer:
    """Create a configured server without starting its event loop.

    ``jobs_enabled=False`` (default) keeps the async ``/jobs`` queue out of
    the API surface entirely, which is what the downloadable client binaries
    want. Enable it only for proxy/Cloudflare deployments.
    """
    return CheckerHTTPServer((host, port), RequestHandler, pipeline=pipeline, model=model, jobs_enabled=jobs_enabled)


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Music copyright checker JSON API server")
    parser.add_argument("--host", default="127.0.0.1", help="Bind address (default: 127.0.0.1).")
    parser.add_argument("--port", type=int, default=8080, help="Bind port (default: 8080).")
    parser.add_argument("--model", default=DEFAULT_OPENCODE_MODEL, help=f"opencode model (default: {DEFAULT_OPENCODE_MODEL}).")
    parser.add_argument("--opencode-server", default=None, help="opencode serve base URL.")
    parser.add_argument("--opencode-binary", default="opencode", help="opencode executable name/path.")
    parser.add_argument(
        "--no-auto-install",
        action="store_false",
        dest="auto_install",
        help="Do not download the opencode CLI if it is missing (it is downloaded via the official installer by default).",
    )
    parser.add_argument(
        "--jobs",
        action="store_true",
        help="Enable the async /jobs queue for proxy/Cloudflare deployments (off by default; local integrations only need /check).",
    )
    parser.add_argument("--timeout", type=float, default=DEFAULT_OPENCODE_TIMEOUT, help="AI research timeout in seconds.")
    parser.add_argument("--no-ai", action="store_true", help="Disable AI for metadata-only API testing.")
    parser.add_argument("--cache-path", default=None, help="SQLite cache path (default: ~/.cache/music-copyright-checker/cache.sqlite3).")
    parser.add_argument("--no-cache", action="store_true", help="Disable metadata and research caching.")
    args = parser.parse_args(argv)

    pipeline = Pipeline(
        opencode_server=args.opencode_server,
        opencode_binary=args.opencode_binary,
        opencode_model=args.model,
        opencode_timeout=args.timeout,
        run_ai_research=not args.no_ai,
        cache_enabled=not args.no_cache,
        cache_path=args.cache_path,
        opencode_auto_install=args.auto_install,
    )
    server = create_server(args.host, args.port, pipeline, model=None if args.no_ai else args.model, jobs_enabled=args.jobs)
    if args.jobs:
        print("Async /jobs endpoints enabled.", file=sys.stderr)
    print(f"music-copyright-checker server listening on http://{args.host}:{args.port}", file=sys.stderr)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
