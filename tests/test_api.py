"""HTTP contracts for the thin FastAPI boundary."""

from fastapi.testclient import TestClient

import api


def test_health_readiness_and_answer_contract(monkeypatch):
    graph = object()
    state = {
        "answer": "[SPEC s1] quoted evidence",
        "answer_status": "evidence_found",
        "query_type": "SPEC",
        "answer_validation": {
            "claims": [{"citation": "[SPEC s1]", "quote": "quoted evidence"}]
        },
    }
    monkeypatch.setattr(api.agent, "run", lambda actual, question: state)

    with TestClient(api.create_app(lambda: graph)) as client:
        assert client.get("/healthz").json() == {"status": "ok"}
        assert client.get("/readyz").json() == {"status": "ready"}
        response = client.post("/v1/ask", json={"question": "  What is required?  "})

    assert response.status_code == 200
    assert response.json() == {
        "answer": "[SPEC s1] quoted evidence",
        "status": "evidence_found",
        "route": "SPEC",
        "citations": ["[SPEC s1]"],
    }


def test_question_validation_rejects_blank_and_oversized_input():
    with TestClient(api.create_app(lambda: object())) as client:
        blank = client.post("/v1/ask", json={"question": "   "})
        oversized = client.post("/v1/ask", json={"question": "x" * 2001})

    assert blank.status_code == oversized.status_code == 422


def test_stale_artifacts_return_service_unavailable(monkeypatch):
    def stale(graph, question):
        raise api.agent.ArtifactFreshnessError("stale index")

    monkeypatch.setattr(api.agent, "run", stale)
    with TestClient(api.create_app(lambda: object())) as client:
        response = client.post("/v1/ask", json={"question": "Where is StallD driven?"})

    assert response.status_code == 503
    assert response.json() == {"detail": "stale index"}
