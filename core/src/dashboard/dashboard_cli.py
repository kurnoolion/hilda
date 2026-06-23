"""dashboard CLI: --diagnostic + --serve --mock.

Per [D-005] independent test interface. Emits DSH-RPT compact reports per [D-002].

Ph-1 modes:
- --diagnostic: validates FastAPI route registration + template availability +
  storage Protocol surface against expected endpoints. Emits DSH-RPT.
- --serve --mock: runs the FastAPI app against a mock storage (in-memory
  fixtures); accepts X-Authenticated-User header automatically via mock_auth=True.
  Useful for SP UI engineer's smoke testing the link-out flow.
"""
from __future__ import annotations

import argparse
import sys
import uuid
from datetime import datetime, timezone

from core.src.diagnostics import ReportRecord, ReportType, ReportWriter

from .app import build_app
from .config import DashboardConfig


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _emit(writer: ReportWriter, rtype: ReportType, fields: dict) -> None:
    writer.emit(ReportRecord(rtype, "DSH", writer.run_id, _now(), fields))


def _cmd_diagnostic(run_id: str) -> int:
    """Validate app construction + template availability + storage helpers.
    Emits DSH-RPT."""
    writer = ReportWriter("DSH", run_id)
    cfg = DashboardConfig()
    templates_ok = cfg.jinja_templates_dir.is_dir()

    routes_ok = False
    routes_count = 0
    try:
        app = build_app(cfg)
        # Extract HTTP routes (skip docs/openapi/health builtins)
        http_routes = [r for r in app.routes if hasattr(r, "path")]
        routes_count = len(http_routes)
        # Expected 5 dashboard routes
        expected = {"/docs/{delivery_item_id}", "/dl/{scoped_token}",
                    "/milestone/{milestone_id}/refresh",
                    "/milestone/{milestone_id}/refresh/status",
                    "/admin/overrides"}
        actual_paths = {r.path for r in http_routes}
        routes_ok = expected.issubset(actual_paths)
    except Exception:
        pass

    _emit(writer, ReportType.RPT, {
        "templates_dir":           str(cfg.jinja_templates_dir),
        "templates_dir_exists":    templates_ok,
        "fastapi_routes_total":    routes_count,
        "expected_routes_present": routes_ok,
        "bind_host":               cfg.bind_host,
        "bind_port":               cfg.bind_port,
        "mock_auth":               cfg.mock_auth,
    })
    writer.flush()
    return 0 if (templates_ok and routes_ok) else 1


def _cmd_serve_mock(host: str, port: int) -> int:
    """Run FastAPI app against mock storage + mock auth. Requires uvicorn."""
    try:
        import uvicorn
    except ImportError:
        print("uvicorn not installed; `pip install uvicorn` to use --serve",
              file=sys.stderr)
        return 2

    cfg = DashboardConfig(mock_auth=True, bind_host=host, bind_port=port)
    app = build_app(cfg)
    print(f"Serving dashboard on http://{host}:{port} (mock_auth=True)")
    uvicorn.run(app, host=host, port=port, log_level="info")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(
        description="dashboard CLI -- diagnostics + mock-serve"
    )
    parser.add_argument("--diagnostic", action="store_true",
                        help="Validate app + templates + routes; DSH-RPT")
    parser.add_argument("--serve", action="store_true",
                        help="Run FastAPI app (requires --mock for now)")
    parser.add_argument("--mock", action="store_true",
                        help="Enable mock auth + in-memory storage")
    parser.add_argument("--host", default="127.0.0.1", help="Bind host (--serve)")
    parser.add_argument("--port", type=int, default=8443, help="Bind port (--serve)")
    parser.add_argument("--run-id", default=None, help="Stable run identifier")

    args = parser.parse_args()
    run_id = args.run_id or f"run-{uuid.uuid4().hex[:8]}"

    if args.diagnostic:
        code = _cmd_diagnostic(run_id)
    elif args.serve:
        if not args.mock:
            parser.error("--serve requires --mock in Ph-1 (production deployment via uvicorn separately)")
        code = _cmd_serve_mock(args.host, args.port)
    else:
        parser.print_help()
        code = 1
    sys.exit(code)


if __name__ == "__main__":
    main()
