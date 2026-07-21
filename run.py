"""Convenience launcher — watches only the app/ folder (not venv).

Prefer this over `uvicorn ... --reload` (which watches the whole repo including
venv and can leave Windows ghost listeners on port 8000 that cancel all requests).
"""
import os
import socket
import urllib.error
import urllib.request

import uvicorn


def _http_healthy(port: int) -> bool:
    """True only if GET /health returns 200 quickly (ghost TCP listeners fail this)."""
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=0.7) as resp:
            return getattr(resp, "status", 200) == 200
    except (urllib.error.URLError, TimeoutError, OSError):
        return False


def _can_bind(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind(("0.0.0.0", port))
            return True
        except OSError:
            return False


def _port() -> int:
    preferred = int(os.getenv("PORT", "8000"))
    candidates = [preferred, 8010, 8020, 8080]
    # Deduplicate while preserving order
    seen: set[int] = set()
    ordered: list[int] = []
    for p in candidates:
        if p not in seen:
            seen.add(p)
            ordered.append(p)

    for port in ordered:
        if _http_healthy(port):
            # A real healthy server already owns this port — try the next one
            continue
        if _can_bind(port):
            return port
    return preferred


if __name__ == "__main__":
    port = _port()
    preferred = int(os.getenv("PORT", "8000"))
    if port != preferred:
        print(
            f"\n*** Port {preferred} is busy or ghosted. "
            f"Serving on http://127.0.0.1:{port}\n"
            f"    Open docs at http://127.0.0.1:{port}/docs "
            f"(not :{preferred}) ***\n",
            flush=True,
        )
    else:
        print(f"\nFlowCast API -> http://127.0.0.1:{port}/docs\n", flush=True)

    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=port,
        reload=True,
        reload_dirs=["app"],
        reload_excludes=["*.pyc", "*/__pycache__/*", "venv/*"],
    )
