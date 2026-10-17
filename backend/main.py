import os
from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from backend.database import init_db
from backend.video_import import shutdown_jobs
from backend.routes import ocr, portfolio, transactions, xirr

app = FastAPI(title="投资回报率记录工具", version="0.1.0")

init_db()
app.add_event_handler("shutdown", shutdown_jobs)

app.include_router(transactions.router, prefix="/api")
app.include_router(xirr.router, prefix="/api")
app.include_router(portfolio.router, prefix="/api")
app.include_router(ocr.router, prefix="/api")


@app.get("/api/health")
def health():
    return {"status": "ok"}


class NoCacheMiddleware:
    """Force no-cache on every response.

    Safari heuristically caches responses that lack explicit cache headers
    (based on Last-Modified/ETag), which caused stale pages to keep loading
    after updates. Stripping validation headers and declaring no-store makes
    every page load fetch fresh content.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                headers = message.get("headers", [])
                filtered = [
                    (k, v)
                    for (k, v) in headers
                    if k.decode("latin-1").lower() not in ("etag", "last-modified", "accept-ranges")
                ]
                cache_headers = [
                    (b"cache-control", b"no-store, no-cache, must-revalidate, max-age=0"),
                    (b"pragma", b"no-cache"),
                    (b"expires", b"0"),
                ]
                message["headers"] = filtered + cache_headers
            await send(message)

        await self.app(scope, receive, send_wrapper)


app.add_middleware(NoCacheMiddleware)


frontend_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "frontend")


@app.api_route("/", methods=["GET", "HEAD"], response_class=HTMLResponse)
def index():
    """Serve index.html directly with no caching, bypassing Safari's stubborn cache.

    Handles both GET and HEAD: Safari uses HEAD requests for cache validation,
    and returning 404 on HEAD caused the page to be treated as missing.
    """
    path = os.path.join(frontend_dir, "index.html")
    with open(path, encoding="utf-8") as f:
        return f.read()


# Mount for all other static files (CSS, JS, images)
app.mount("/", StaticFiles(directory=frontend_dir, html=False), name="frontend")
