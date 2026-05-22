import os
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from backend.database import init_db
from backend.routes import ocr, portfolio, transactions, xirr

app = FastAPI(title="投资回报率记录工具", version="0.1.0")

init_db()

app.include_router(transactions.router, prefix="/api")
app.include_router(xirr.router, prefix="/api")
app.include_router(portfolio.router, prefix="/api")
app.include_router(ocr.router, prefix="/api")


@app.get("/api/health")
def health():
    return {"status": "ok"}


frontend_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "frontend")
app.mount("/", StaticFiles(directory=frontend_dir, html=True), name="frontend")
