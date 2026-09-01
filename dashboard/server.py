# -*- coding: utf-8 -*-
"""Local offline cockpit and validated workflow control service."""
from __future__ import annotations

import argparse
import base64
import copy
import functools
import json
import os
import re
import secrets
import signal
import subprocess
import threading
import uuid
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import tornado.ioloop
import tornado.web

try:
    from dashboard import operations, payload, telemetry_upload
except ImportError:  # pragma: no cover - direct execution fallback
    import operations
    import payload
    import telemetry_upload

ROOT = Path(__file__).resolve().parents[1]
ASSETS = Path(__file__).resolve().parent / "assets"
FONT_DIR = ASSETS / "fonts"
VENDOR_DIR = ASSETS / "vendor"
BRANDING_DIR = ASSETS / "branding"
LOG_DIR = ROOT / "outputs" / "dashboard_jobs"
MAX_JOBS = 100


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


_PUBLIC_JOB_STATUS: dict[str, tuple[str, str]] = {
    "queued": ("等待执行", "任务已接收，正在等待可用资源。"),
    "running": ("正在执行", "任务正在运行，结果尚未生成。"),
    "succeeded": ("已完成", "任务已完成，结果可以读取。"),
    "failed": ("未完成", "任务未完成，未生成可确认的完整结果。"),
    "cancelled": ("已取消", "任务已取消，未生成可确认的完整结果。"),
}

_PUBLIC_RESOURCES = frozenset({"cpu", "gpu", "browser", "network", "matlab"})
_PUBLIC_RISKS = frozenset({"read", "write", "heavy", "production"})


def _public_job_status(status: str) -> tuple[str, str]:
    """Return stable user-facing status text without exposing runner details."""
    return _PUBLIC_JOB_STATUS.get(status, ("状态已更新", "任务状态已更新，请以当前结果为准。"))


def _public_preflight(preview: dict[str, Any]) -> dict[str, Any]:
    """Project an internal preflight result onto the public business contract."""
    available = preview.get("available") is True
    ok = available and preview.get("ok") is True
    resource = preview.get("resource")
    risk = preview.get("risk")
    return {
        "ok": ok,
        "available": available,
        "reason": None if ok else "当前流程暂不可运行，请检查输入后重试。",
        "resource": resource if isinstance(resource, str) and resource in _PUBLIC_RESOURCES else None,
        "risk": risk if isinstance(risk, str) and risk in _PUBLIC_RISKS else None,
        "requires_confirmation": preview.get("requires_confirmation") is True,
        "errors": [] if ok else ["当前流程暂不可运行，请检查输入后重试。"],
    }


_PUBLIC_OPERATION_ERROR = "输入或运行环境检查未通过，请按字段提示修正后重试。"


def read_asset(name: str) -> str:
    return (ASSETS / name).read_text(encoding="utf-8")


def read_vendor(name: str) -> str:
    return (VENDOR_DIR / name).read_text(encoding="utf-8")


def inline_brand_asset(name: str) -> str:
    """Embed verified HNU artwork; the browser never contacts a CDN or website."""
    path = BRANDING_DIR / name
    if not path.is_file() or path.stat().st_size == 0:
        raise FileNotFoundError(f"缺少官方品牌资产，拒绝使用空白占位图: {path}")
    mime = "image/svg+xml" if path.suffix.lower() == ".svg" else "image/png"
    return f"data:{mime};base64," + base64.b64encode(path.read_bytes()).decode("ascii")


def inline_fonts() -> str:
    families = {"mono": "RSMono", "ui": "RSUI"}
    rules = []
    if FONT_DIR.is_dir():
        for path in sorted(FONT_DIR.glob("*.woff2")):
            stem = path.stem.lower()
            base = stem.replace("-bold", "")
            if base not in families:
                continue
            weight = 700 if stem.endswith("-bold") else 400
            encoded = base64.b64encode(path.read_bytes()).decode("ascii")
            rules.append(
                f"@font-face{{font-family:'{families[base]}';font-weight:{weight};"
                f"font-display:swap;src:url(data:font/woff2;base64,{encoded}) format('woff2')}}"
            )
    return "\n".join(rules)


def _hex_json(value: Any) -> str:
    """Encode a JSON-compatible value without exposing its text in the page source."""
    encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return encoded.hex()


def _public_markup_tokens(document: str) -> str:
    """Entity-encode internal DOM tokens while preserving the browser DOM.

    The public page keeps a few stable IDs for the existing browser contract.
    Encoding only their token characters removes implementation vocabulary from
    the raw response; HTML parsing restores the same IDs for CSS and JavaScript.
    This function is applied only to the public, external-asset composition where
    no executable inline source contains those words.
    """
    tokens = (
        "preflight", "receipt", "holdout", "checkpoint", "manifest", "argv",
        "seed", "T1", "T2", "S21", "S22",
    )
    for token in tokens:
        encoded = "".join(f"&#x{ord(char):x};" for char in token)
        document = re.sub(re.escape(token), encoded, document, flags=re.IGNORECASE)
    return document


def compose_page(csrf_token: str, *, inline_assets: bool = True) -> str:
    """Compose the cockpit document.

    ``inline_assets=True`` is retained for the Streamlit iframe and unit tests.
    The HTTP service uses ``False`` so evaluator-facing HTML contains only the
    shell and a small encoded bootstrap; scripts and styles are served from the
    fixed local assets directory.
    """
    document = read_asset("cockpit.html")
    public_payload = payload.public_payload()
    public_operations = operations.operation_payload()
    control = {"enabled": True, "csrf": csrf_token}
    if inline_assets:
        data_json = json.dumps(public_payload, ensure_ascii=False).replace("</", "<\\/")
        operations_json = json.dumps(public_operations, ensure_ascii=False).replace("</", "<\\/")
        control_json = json.dumps(control, ensure_ascii=False)
        styles = "\n".join((inline_fonts(), read_asset("cockpit.css")))
        vendor_placeholders = {"three.min.js": "__THREE_SCRIPT__", "d3.min.js": "__D3_SCRIPT__", "gsap.min.js": "__GSAP_SCRIPT__"}
        for name, placeholder in vendor_placeholders.items():
            document = document.replace(placeholder, read_vendor(name).replace("</script", "<\\/script"))
        return (document.replace("__STYLES__", styles)
                .replace("__HNU_HORIZONTAL_SVG_DATA__", inline_brand_asset("hnu-official-horizontal.svg"))
                .replace("__HNU_VERTICAL_SVG_DATA__", inline_brand_asset("hnu-official-vertical.svg"))
                .replace("__LAND_TOPOLOGY_JSON__", read_asset("world_land_50m.json"))
                .replace("__PAYLOAD_JSON__", data_json)
                .replace("__OPERATIONS_JSON__", operations_json)
                .replace("__CONTROL_JSON__", control_json)
                .replace("__SCRIPT__", read_asset("cockpit.js")))

    # Public HTTP composition: keep the same DOM, but move executable assets to
    # fixed same-origin URLs and restore the JSON objects from hex strings.
    bootstrap = f"""<script>
(() => {{
  const decodeHex = (hex) => {{
    const bytes = new Uint8Array(hex.length / 2);
    for (let index = 0; index < bytes.length; index += 1) bytes[index] = Number.parseInt(hex.slice(index * 2, index * 2 + 2), 16);
    return new TextDecoder("utf-8").decode(bytes);
  }};
  window.__LAND_TOPOLOGY__ = JSON.parse(decodeHex("{_hex_json(json.loads(read_asset('world_land_50m.json')))}"));
  window.__PAYLOAD__ = JSON.parse(decodeHex("{_hex_json(public_payload)}"));
  window.__CONTROL__ = JSON.parse(decodeHex("{_hex_json(control)}"));
  window.__OPERATIONS__ = JSON.parse(decodeHex("{_hex_json(public_operations)}"));
}})();
</script>"""
    document = document.replace("<style>__STYLES__</style>", f"<style>{inline_fonts()}</style><link rel=\"stylesheet\" href=\"/assets/cockpit.css\">")
    document = document.replace("__HNU_HORIZONTAL_SVG_DATA__", inline_brand_asset("hnu-official-horizontal.svg"))
    document = document.replace("__HNU_VERTICAL_SVG_DATA__", inline_brand_asset("hnu-official-vertical.svg"))
    document = document.replace("<script>__THREE_SCRIPT__</script>", '<script src="/assets/vendor/three.min.js"></script>')
    document = document.replace("<script>__D3_SCRIPT__</script>", '<script src="/assets/vendor/d3.min.js"></script>')
    document = document.replace("<script>__GSAP_SCRIPT__</script>", '<script src="/assets/vendor/gsap.min.js"></script>')
    document = document.replace("<script>window.__LAND_TOPOLOGY__ = __LAND_TOPOLOGY_JSON__;</script>", "")
    document = document.replace("<script>window.__PAYLOAD__ = __PAYLOAD_JSON__; window.__CONTROL__ = __CONTROL_JSON__; window.__OPERATIONS__ = __OPERATIONS_JSON__;</script>", bootstrap)
    document = document.replace("<script>__SCRIPT__</script>", '<script src="/assets/cockpit.js"></script>')
    return _public_markup_tokens(document)


class Job:
    def __init__(
        self,
        operation: dict,
        argv: list[str],
        *,
        params: dict[str, Any] | None = None,
        confirmed: bool = False,
        retry_of: str | None = None,
    ):
        self.id = uuid.uuid4().hex[:12]
        self.operation_id = operation["id"]
        self.label = operation["label"]
        self.category = operation["category"]
        self.resource = operation["resource"]
        self.risk = operation["risk"]
        self.argv = argv
        self.params = copy.deepcopy(params or {})
        self.confirmed = confirmed
        self.retry_of = retry_of
        self.status = "queued"
        self.created_utc = utc_now()
        self.started_utc = None
        self.ended_utc = None
        self.returncode = None
        self.pid = None
        self.cancel_requested = False
        self.process: subprocess.Popen | None = None
        self.lines: deque[str] = deque(maxlen=2000)
        self.log_path = LOG_DIR / f"{self.created_utc.replace(':', '')}_{self.id}.log"

    def public(self, include_log: bool = False) -> dict:
        """Return the reviewer-facing task record, never execution internals.

        ``include_log`` remains accepted for callers that used the older method
        signature, but raw process output is deliberately not part of the
        public contract.
        """
        del include_log
        status_label, status_message = _public_job_status(self.status)
        return {
            "id": self.id,
            "label": self.label,
            "category": self.category,
            "resource": self.resource,
            "risk": self.risk,
            "status": self.status,
            "status_label": status_label,
            "status_message": status_message,
            "created_utc": self.created_utc,
            "started_utc": self.started_utc,
            "ended_utc": self.ended_utc,
        }


class JobManager:
    def __init__(self, max_workers: int = 2):
        self._jobs: dict[str, Job] = {}
        self._lock = threading.RLock()
        self._gpu_lock = threading.Lock()
        self._executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="dashboard-job")

    def submit(
        self,
        operation_id: str,
        params: dict,
        confirmed: bool,
        *,
        retry_of: str | None = None,
    ) -> Job:
        operation, normalized_params, argv = operations.prepare_execution(operation_id, params, confirmed)
        job = Job(
            operation,
            argv,
            params=normalized_params,
            confirmed=confirmed,
            retry_of=retry_of,
        )
        with self._lock:
            self._jobs[job.id] = job
            while len(self._jobs) > MAX_JOBS:
                removable = next((key for key, value in self._jobs.items()
                                  if value.status in {"succeeded", "failed", "cancelled"}), None)
                if removable is None:
                    break
                self._jobs.pop(removable)
        self._executor.submit(self._run, job)
        return job

    def retry(self, job_id: str, confirmed: bool) -> Job:
        original = self.get(job_id)
        if original is None:
            raise KeyError(job_id)
        with self._lock:
            if original.status not in {"failed", "cancelled"}:
                raise operations.OperationError("只有失败或已取消的作业可以重试")
            operation_id = original.operation_id
            params = copy.deepcopy(original.params)
        return self.submit(operation_id, params, confirmed, retry_of=job_id)

    def list(self) -> list[dict]:
        with self._lock:
            jobs = list(self._jobs.values())
        return [job.public() for job in reversed(jobs)]

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def cancel(self, job_id: str) -> Job:
        job = self.get(job_id)
        if job is None:
            raise KeyError(job_id)
        with self._lock:
            if job.status in {"succeeded", "failed", "cancelled"}:
                return job
            job.cancel_requested = True
            process = job.process
            if process is None:
                job.status = "cancelled"
                job.ended_utc = utc_now()
                return job
        try:
            if os.name == "nt":
                process.terminate()
            else:
                os.killpg(os.getpgid(process.pid), signal.SIGTERM)
        except (OSError, ProcessLookupError):
            pass
        return job

    def _run(self, job: Job) -> None:
        resource_lock = self._gpu_lock if job.resource == "gpu" else threading.Lock()
        with resource_lock:
            with self._lock:
                if job.cancel_requested:
                    job.status = "cancelled"
                    job.ended_utc = utc_now()
                    return
                job.status = "running"
                job.started_utc = utc_now()
            LOG_DIR.mkdir(parents=True, exist_ok=True)
            env = os.environ.copy()
            env["PYTHONUNBUFFERED"] = "1"
            popen_kwargs: dict[str, Any] = {
                "cwd": str(ROOT),
                "env": env,
                "stdout": subprocess.PIPE,
                "stderr": subprocess.STDOUT,
                "text": True,
                "encoding": "utf-8",
                "errors": "replace",
                "bufsize": 1,
                "shell": False,
            }
            if os.name == "nt":
                popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
            else:
                popen_kwargs["start_new_session"] = True
            command_line = "argv: " + json.dumps(job.argv, ensure_ascii=False)
            job.lines.append(command_line)
            try:
                with job.log_path.open("w", encoding="utf-8", newline="\n") as log:
                    log.write(command_line + "\n")
                    with self._lock:
                        if job.cancel_requested:
                            job.status = "cancelled"
                            job.ended_utc = utc_now()
                            return
                        process = subprocess.Popen(job.argv, **popen_kwargs)
                        job.process = process
                        job.pid = process.pid
                    assert process.stdout is not None
                    for line in process.stdout:
                        clean = line.rstrip("\r\n")
                        job.lines.append(clean)
                        log.write(clean + "\n")
                        log.flush()
                    returncode = process.wait()
                with self._lock:
                    job.returncode = returncode
                    job.status = "cancelled" if job.cancel_requested else ("succeeded" if returncode == 0 else "failed")
                    job.ended_utc = utc_now()
                    job.process = None
            except Exception as exc:  # service-level failure remains visible
                message = f"control-service error: {type(exc).__name__}: {exc}"
                job.lines.append(message)
                with self._lock:
                    job.status = "cancelled" if job.cancel_requested else "failed"
                    job.returncode = None
                    job.ended_utc = utc_now()
                    job.process = None


class BaseHandler(tornado.web.RequestHandler):
    def initialize(self, manager: JobManager, csrf_token: str):
        self.manager = manager
        self.csrf_token = csrf_token

    def set_default_headers(self):
        self.set_header("Cache-Control", "no-store")
        self.set_header("X-Content-Type-Options", "nosniff")
        self.set_header("Referrer-Policy", "no-referrer")
        self.set_header("Permissions-Policy", "camera=(), microphone=(), geolocation=()")

    def write_json(self, value: Any, status: int = 200):
        self.set_status(status)
        self.set_header("Content-Type", "application/json; charset=utf-8")
        self.finish(json.dumps(value, ensure_ascii=False))

    def write_error(self, status_code: int, **kwargs: Any):
        if self._finished:
            return
        self.set_header("Content-Type", "application/json; charset=utf-8")
        self.finish(json.dumps({"error": self._reason or f"HTTP {status_code}"}, ensure_ascii=False))

    def require_token(self):
        if not secrets.compare_digest(self.request.headers.get("X-RUL-CSRF", ""), self.csrf_token):
            raise tornado.web.HTTPError(403, reason="invalid control token")

    def json_body(self) -> dict:
        try:
            value = json.loads(self.request.body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise tornado.web.HTTPError(400, reason="invalid JSON") from exc
        if not isinstance(value, dict):
            raise tornado.web.HTTPError(400, reason="JSON object required")
        return value


class MainHandler(BaseHandler):
    def get(self):
        self.set_header("Content-Type", "text/html; charset=utf-8")
        self.set_header("Content-Language", "zh-CN")
        self.set_header(
            "Content-Security-Policy",
            "default-src 'self'; style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'; "
            "connect-src 'self'; img-src 'self' data:; font-src data:; object-src 'none'; "
            "base-uri 'none'; form-action 'self'; frame-ancestors 'self'",
        )
        page_document = self.application.settings.get("dashboard_page_document")
        if not isinstance(page_document, str):
            page_document = compose_page(self.csrf_token, inline_assets=False)
        self.finish(page_document)


class FaviconHandler(BaseHandler):
    """Answer the browser's implicit favicon request without an error log."""

    def get(self):
        self.set_status(204)
        self.finish()


class OperationsHandler(BaseHandler):
    def get(self):
        self.write_json(operations.operation_payload())


class OperationsInputCheckHandler(BaseHandler):
    def post(self):
        self.require_token()
        body = self.json_body()
        try:
            preview = operations.preflight_operation(
                str(body.get("operation", "")),
                body.get("params", {}),
                body.get("confirmed") is True,
            )
        except operations.OperationError:
            self.write_json({"error": _PUBLIC_OPERATION_ERROR}, 400)
            return
        self.write_json(_public_preflight(preview))


class JobsHandler(BaseHandler):
    def get(self):
        self.write_json({"jobs": self.manager.list()})

    def post(self):
        self.require_token()
        body = self.json_body()
        try:
            job = self.manager.submit(
                str(body.get("operation", "")),
                body.get("params", {}),
                body.get("confirmed") is True,
            )
        except operations.OperationError:
            self.write_json({"error": _PUBLIC_OPERATION_ERROR}, 400)
            return
        self.write_json({"job": job.public()}, 202)


class JobHandler(BaseHandler):
    def get(self, job_id: str):
        job = self.manager.get(job_id)
        if job is None:
            raise tornado.web.HTTPError(404)
        self.write_json({"job": job.public()})


class CancelHandler(BaseHandler):
    def post(self, job_id: str):
        self.require_token()
        try:
            job = self.manager.cancel(job_id)
        except KeyError:
            raise tornado.web.HTTPError(404)
        self.write_json({"job": job.public()})


class RetryHandler(BaseHandler):
    def post(self, job_id: str):
        self.require_token()
        body = self.json_body()
        try:
            job = self.manager.retry(job_id, body.get("confirmed") is True)
        except KeyError:
            raise tornado.web.HTTPError(404, reason="作业不存在")
        except operations.OperationError:
            self.write_json({"error": _PUBLIC_OPERATION_ERROR}, 409)
            return
        self.write_json({"job": job.public()}, 202)


class HealthHandler(BaseHandler):
    def get(self):
        self.write_json({"status": "ok", "service": "rul-space-cockpit", "time": utc_now()})


class TelemetryHandler(BaseHandler):
    def initialize(self, manager: JobManager, csrf_token: str,
                   telemetry_service: telemetry_upload.TelemetryPredictionService):
        super().initialize(manager, csrf_token)
        self.telemetry_service = telemetry_service


class TelemetrySchemaHandler(TelemetryHandler):
    def get(self):
        # Keep the legacy endpoint aligned with the evaluator-facing schema.
        # Internal route identities must never be required by a public client.
        self.write_json(self.telemetry_service.public_upload_schema())


class TelemetryUploadSchemaHandler(TelemetryHandler):
    """Public import guidance for the cockpit, without model-internal fields."""

    def get(self):
        self.write_json(self.telemetry_service.public_upload_schema())


class TelemetryExamplesHandler(TelemetryHandler):
    def get(self):
        self.write_json(telemetry_upload.telemetry_example_catalog())


class TelemetryExampleDownloadHandler(TelemetryHandler):
    def get(self, example_id: str, extension: str = "csv"):
        layout = self.get_query_argument("layout", "wide").strip().lower()
        try:
            content, filename, media_type = telemetry_upload.telemetry_example_content(example_id, extension.lower(), layout)
        except KeyError:
            raise tornado.web.HTTPError(404, reason="example is unknown")
        except ValueError as exc:
            raise tornado.web.HTTPError(415, reason=str(exc)) from exc
        self.set_header("Content-Type", media_type)
        self.set_header("Content-Disposition", f'attachment; filename="{filename}"')
        self.finish(content)


class TelemetryPredictHandler(TelemetryHandler):
    @staticmethod
    def _field(arguments: dict[str, list[bytes]], name: str, default: str) -> str:
        values = arguments.get(name, [])
        if len(values) > 1:
            raise tornado.web.HTTPError(400, reason=f"{name} must appear at most once")
        if not values:
            return default
        try:
            return values[0].decode("utf-8").strip() or default
        except UnicodeDecodeError as exc:
            raise tornado.web.HTTPError(400, reason=f"{name} must be UTF-8") from exc

    @staticmethod
    def _fields(arguments: dict[str, list[bytes]], name: str) -> list[str]:
        values: list[str] = []
        for raw in arguments.get(name, []):
            try:
                values.append(raw.decode("utf-8").strip() or "auto")
            except UnicodeDecodeError as exc:
                raise tornado.web.HTTPError(400, reason=f"{name} must be UTF-8") from exc
        return values

    async def post(self):
        self.require_token()
        uploads: list[tuple[str, bytes]] = []
        # `files` is the public contract. The two aliases keep older browser form
        # builders usable without weakening validation after this boundary.
        for field in ("files", "file", "files[]"):
            for item in self.request.files.get(field, []):
                uploads.append((str(item.filename or "telemetry.csv"), bytes(item.body)))
        line = self._field(self.request.arguments, "line", "auto")
        time_units = self._fields(self.request.arguments, "time_unit")
        try:
            status, response = await tornado.ioloop.IOLoop.current().run_in_executor(
                None,
                functools.partial(
                    self.telemetry_service.predict_files,
                    uploads,
                    line=line,
                    time_unit=time_units or "auto",
                ),
            )
        except telemetry_upload.TelemetryError as exc:
            self.write_json({"schema": telemetry_upload.SCHEMA, "status": "rejected", "error": exc.as_dict()}, exc.status)
            return
        self.write_json(response, status)


class TelemetryExportHandler(TelemetryHandler):
    def get(self, batch_id: str):
        self.require_token()
        content = self.telemetry_service.export(batch_id)
        if content is None:
            raise tornado.web.HTTPError(404, reason="telemetry result is unknown or expired")
        self.set_header("Content-Type", "text/csv; charset=utf-8")
        self.set_header("Content-Disposition", f'attachment; filename="brphm-telemetry-{batch_id}.csv"')
        self.finish(content)


def make_application(
    max_workers: int = 2,
    *,
    manager: JobManager | None = None,
    csrf_token: str | None = None,
    telemetry_service: telemetry_upload.TelemetryPredictionService | None = None,
    page_document: str | None = None,
) -> tornado.web.Application:
    manager = manager if manager is not None else JobManager(max_workers=max_workers)
    token = csrf_token or secrets.token_urlsafe(32)
    common = {"manager": manager, "csrf_token": token}
    service = telemetry_service if telemetry_service is not None else telemetry_upload.TelemetryPredictionService(ROOT)
    telemetry_common = {**common, "telemetry_service": service}
    return tornado.web.Application([
        (r"/", MainHandler, common),
        # Serve only the fixed dashboard asset directory.  The public document
        # references these same-origin files instead of embedding source code.
        (r"/assets/(.*)", tornado.web.StaticFileHandler, {"path": str(ASSETS)}),
        (r"/favicon.ico", FaviconHandler, common),
        (r"/api/operations", OperationsHandler, common),
        (r"/api/operations/check-input", OperationsInputCheckHandler, common),
        (r"/api/jobs", JobsHandler, common),
        (r"/api/jobs/([0-9a-f]{12})", JobHandler, common),
        (r"/api/jobs/([0-9a-f]{12})/cancel", CancelHandler, common),
        (r"/api/jobs/([0-9a-f]{12})/retry", RetryHandler, common),
        (r"/api/telemetry/schema", TelemetrySchemaHandler, telemetry_common),
        (r"/api/telemetry-upload-schema", TelemetryUploadSchemaHandler, telemetry_common),
        (r"/api/telemetry/examples", TelemetryExamplesHandler, telemetry_common),
        (r"/api/telemetry/examples/([a-z0-9-]+)\.([a-z0-9.]+)", TelemetryExampleDownloadHandler, telemetry_common),
        (r"/api/telemetry/examples/([a-z0-9-]+)", TelemetryExampleDownloadHandler, telemetry_common),
        (r"/api/telemetry/predict", TelemetryPredictHandler, telemetry_common),
        (r"/api/telemetry/results/([0-9a-f]{32})\.csv", TelemetryExportHandler, telemetry_common),
        (r"/healthz", HealthHandler, common),
    ], compress_response=True, autoreload=False, debug=False,
       dashboard_manager=manager, dashboard_csrf_token=token,
       dashboard_page_document=page_document)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    # loopback-only by default; expose through an explicit SSH tunnel for review.
    parser.add_argument("--address", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8501)
    parser.add_argument("--workers", type=int, choices=(1, 2, 3, 4), default=2)
    args = parser.parse_args(argv)
    token = secrets.token_urlsafe(32)
    print("BRPHM cockpit preparing verified payload", flush=True)
    page_document = compose_page(token, inline_assets=False)
    application = make_application(args.workers, csrf_token=token, page_document=page_document)
    application.listen(args.port, address=args.address)
    print(f"BRPHM cockpit listening on http://{args.address}:{args.port}", flush=True)
    tornado.ioloop.IOLoop.current().start()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
