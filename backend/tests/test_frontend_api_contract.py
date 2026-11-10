"""Keep literal frontend HTTP calls aligned with registered backend operations."""
from pathlib import Path
import re
from itertools import chain

from app.main import app


def _route_regex(path: str) -> re.Pattern[str]:
    segments = ["[^/]+" if segment.startswith("{") and segment.endswith("}")
                else re.escape(segment) for segment in path.split("/")]
    return re.compile("^" + "/".join(segments) + "$")


def test_frontend_http_calls_match_registered_backend_routes():
    frontend = Path(__file__).resolve().parents[2] / "frontend" / "src"
    operations = {
        (method.upper(), getattr(route, "path", ""))
        for route in app.routes
        for method in (getattr(route, "methods", None) or set())
        if getattr(route, "path", "").startswith("/api/")
    }
    patterns = [(method, _route_regex(path)) for method, path in operations]
    misses = []
    for source in chain(frontend.rglob("*.js"), frontend.rglob("*.jsx")):
        content = source.read_text(encoding="utf-8")
        for match in re.finditer(
            r"\bhttp\.(get|post|put|patch|delete)\(\s*(`[^`]*`|'[^']*'|\"[^\"]*\")",
            content,
            re.DOTALL,
        ):
            method, literal = match.groups()
            path = literal[1:-1].split("?", 1)[0]
            path = re.sub(r"\$\{[^}]+\}", "DYNAMIC", path)
            route_path = path if path.startswith("/api/") else f"/api{path}"
            candidate_paths = [route_path]
            if route_path == "/api/reports/export/DYNAMIC":
                candidate_paths.extend(f"/api/reports/export/{kind}"
                                       for kind in ("attendance", "tasks", "employees", "timesheets"))
            if not any(op_method == method.upper() and pattern.fullmatch(candidate)
                       for op_method, pattern in patterns for candidate in candidate_paths):
                line = content.count("\n", 0, match.start()) + 1
                misses.append(f"{source.relative_to(frontend)}:{line} {method.upper()} {route_path}")

    assert not misses, "Frontend calls do not match backend routes:\n" + "\n".join(misses)
