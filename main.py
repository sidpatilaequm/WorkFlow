from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from database import engine, Base
from routers import workflows, requests, stages, approvals, analytics, auth

Base.metadata.create_all(bind=engine)

app = FastAPI(
    title="Document Workflow Engine",
    description="Folderit-style approval workflow engine with FastAPI + MySQL",
    version="1.0.0"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router,       prefix="/api/auth",      tags=["Auth"])
app.include_router(workflows.router,  prefix="/api/workflows", tags=["Workflows"])
app.include_router(requests.router,   prefix="/api/requests",  tags=["Requests"])
app.include_router(stages.router,     prefix="/api/stages",    tags=["Stages"])
app.include_router(approvals.router,  prefix="/api/approvals", tags=["Approvals"])
app.include_router(analytics.router,  prefix="/api/analytics", tags=["Analytics"])

@app.get("/")
def root():
    return {"status": "ok", "message": "Workflow Engine API running"}
