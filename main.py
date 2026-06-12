import logging
import os
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse
from dotenv import load_dotenv
from database import engine, Base
from routers import workflows, requests, stages, approvals, analytics, auth
from services.escalation import start_scheduler, stop_scheduler

load_dotenv()

logging.basicConfig(
    level=logging.DEBUG if os.getenv("DEBUG", "").lower() in ("1", "true") else logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
)
logger = logging.getLogger(__name__)

Base.metadata.create_all(bind=engine)

DEBUG = os.getenv("DEBUG", "").lower() in ("1", "true")
FRONTEND_URL = os.getenv("FRONTEND_URL", "http://localhost:3000")


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting Workflow Engine API")
    start_scheduler()
    yield
    stop_scheduler()
    logger.info("Shutdown complete")


app = FastAPI(
    title="Document Workflow Engine",
    description="Folderit-style approval workflow engine with FastAPI + MySQL",
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
)

# Ensure uploads directory exists
if not os.path.exists("uploads"):
    os.makedirs("uploads")

app.mount("/api/uploads", StaticFiles(directory="uploads"), name="uploads")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"] if DEBUG else [FRONTEND_URL],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router,       prefix="/api/auth",      tags=["Auth"])
app.include_router(workflows.router,  prefix="/api/workflows", tags=["Workflows"])
app.include_router(requests.router,   prefix="/api/requests",  tags=["Requests"])
app.include_router(stages.router,     prefix="/api/stages",    tags=["Stages"])
app.include_router(approvals.router,  prefix="/api/approvals", tags=["Approvals"])
app.include_router(analytics.router,  prefix="/api/analytics", tags=["Analytics"])


@app.get("/health")
def health():
    return {"status": "ok", "version": "1.0.0"}


@app.exception_handler(Exception)
async def global_exception_handler(request, exc):
    logger.error("Unhandled exception: %s", exc, exc_info=True)
    return JSONResponse(status_code=500, content={"detail": "Internal server error"})
