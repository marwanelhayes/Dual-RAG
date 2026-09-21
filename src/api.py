"""Expose ChipVer's existing graph through a minimal validated HTTP boundary.

Run with ``uvicorn api:app --app-dir src --host 0.0.0.0 --port 8000``.
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel, Field, field_validator

import agent


MAX_QUESTION_CHARS = 2000


class AskRequest(BaseModel):
    """Validate and normalize one user question at the trust boundary."""

    question: str = Field(min_length=1, max_length=MAX_QUESTION_CHARS)

    @field_validator("question")
    @classmethod
    def strip_question(cls, value):
        """Reject whitespace-only questions and return normalized text."""
        value = value.strip()
        if not value:
            raise ValueError("question must not be blank")
        return value


class AskResponse(BaseModel):
    """Return public answer fields without exposing retrieved source context."""

    answer: str
    status: str
    route: str
    citations: list[str]


def create_app(graph_factory=agent.build_graph):
    """Create an application whose graph is built once for all requests."""

    @asynccontextmanager
    async def lifespan(application):
        """Initialize the compiled graph before accepting traffic."""
        application.state.graph = graph_factory()
        yield

    application = FastAPI(title="ChipVer", version="1.0", lifespan=lifespan)

    @application.get("/healthz")
    def healthz():
        """Report process liveness without touching models or artifacts."""
        return {"status": "ok"}

    @application.get("/readyz")
    def readyz(request: Request):
        """Report whether application startup created the shared graph."""
        if not hasattr(request.app.state, "graph"):
            raise HTTPException(status_code=503, detail="graph is not ready")
        return {"status": "ready"}

    @application.post("/v1/ask", response_model=AskResponse)
    def ask_question(payload: AskRequest, request: Request):
        """Run one validated question through the existing ChipVer graph."""
        try:
            state = agent.run(request.app.state.graph, payload.question)
        except agent.ArtifactFreshnessError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        claims = state.get("answer_validation", {}).get("claims", [])
        citations = list(dict.fromkeys(
            claim.get("citation") for claim in claims if claim.get("citation")))
        return AskResponse(answer=state["answer"], status=state["answer_status"],
                           route=state["query_type"], citations=citations)

    return application


app = create_app()
