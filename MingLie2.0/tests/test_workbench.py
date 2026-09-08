from pathlib import Path

import pytest

from app.agent import RecruiterAgent
from app.factory import create_app


@pytest.fixture()
def app(tmp_path: Path):
    app = create_app({"TESTING": True, "DATABASE": tmp_path / "test.db", "SEARCH_PROVIDER": "demo"})
    yield app


def create_job(client):
    response = client.post("/api/v1/jobs", json={"name": "Python 后端工程师", "jd": "上海，5年工作经验，熟悉 Python、SQL、Docker"})
    assert response.status_code == 201
    return response.get_json()["data"]


def test_health_and_end_to_end_ingestion_scoring(app):
    client = app.test_client()
    assert client.get("/api/v1/health").get_json()["data"]["status"] == "ok"
    job = create_job(client)
    assert client.post(f"/api/v1/jobs/{job['id']}/analyze-jd").status_code == 201
    payload = {"job_id": job["id"], "source": "test", "candidates": [{"external_id": "p-1", "name": "王工", "location": "上海", "experience_years": 6, "skills": ["Python", "SQL", "Docker"], "summary": "Python 后端开发"}]}
    first = client.post("/api/v1/ingestions", json=payload, headers={"Idempotency-Key": "same-request"})
    assert first.status_code == 201 and first.get_json()["data"]["ingestion"]["created_count"] == 1
    replay = client.post("/api/v1/ingestions", json=payload, headers={"Idempotency-Key": "same-request"})
    assert replay.status_code == 200 and replay.get_json()["data"]["replayed"] is True
    scores = client.post(f"/api/v1/jobs/{job['id']}/score-runs")
    assert scores.status_code == 201 and scores.get_json()["data"]["scores"][0]["total_score"] >= 75
    ranked = client.get(f"/api/v1/jobs/{job['id']}/candidates").get_json()["data"]
    assert ranked[0]["name"] == "王工" and ranked[0]["recommendation"] == "contact"
    assert client.patch(f"/api/v1/candidates/{ranked[0]['id']}/pipeline", json={"stage": "contacted", "note": "已沟通"}).status_code == 200
    assert client.post(f"/api/v1/candidates/{ranked[0]['id']}/feedback", json={"kind": "positive", "note": "愿意了解"}).status_code == 201


def test_demo_workflow_and_agent_are_auditable(app):
    client = app.test_client(); job = create_job(client)
    workflow_id = app.extensions["workflows"].start(job["id"], trigger="test", background=False)
    workflow = app.extensions["store"].get_workflow(workflow_id)
    assert workflow["status"] == "completed"
    assert [x["tool_name"] for x in workflow["steps"]] == ["analyze_jd", "scrape_liepin", "ingest_candidates", "score_candidates"]
    result = RecruiterAgent(app.extensions["store"], app.extensions["workflows"]).run(job["id"], "寻找并排序候选人", background=False)
    assert result["agent_run"]["status"] == "completed"
    assert "评分" in result["agent_run"]["summary"]


def test_validation_error_shape(app):
    response = app.test_client().post("/api/v1/jobs", json={"jd": "no title"})
    assert response.status_code == 422
    assert response.get_json()["error"]["code"] == "validation_error"
