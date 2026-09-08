from __future__ import annotations

from flask import Blueprint, current_app, jsonify, request

from app.agent import RecruiterAgent
from app.domain import PIPELINE_STAGES
from app.services.ingestion import ingest_candidates
from app.services.jd import analyze_jd
from app.services.scoring import score_job

api = Blueprint("api", __name__, url_prefix="/api/v1")


def data(value, status=200): return jsonify({"data": value}), status
def error(code, message, status=400, details=None): return jsonify({"error": {"code": code, "message": message, "details": details or {}}}), status
def store(): return current_app.extensions["store"]
def workflows(): return current_app.extensions["workflows"]
def agent(): return RecruiterAgent(store(), workflows())
def body(): return request.get_json(silent=True) or {}
def job_or_404(job_id):
    value = store().get_job(job_id)
    return value


@api.get("/health")
def health(): return data({"status": "ok", "database": str(current_app.config["DATABASE"]), "search_provider": current_app.config["SEARCH_PROVIDER"]})

@api.route("/jobs", methods=["GET", "POST"])
def jobs():
    if request.method == "GET": return data(store().list_jobs())
    payload = body(); name, jd = str(payload.get("name", "")).strip(), str(payload.get("jd", "")).strip()
    if not name: return error("validation_error", "name is required", 422, {"name": "required"})
    return data(store().create_job(name, jd), 201)

@api.route("/jobs/<int:job_id>", methods=["GET", "PATCH"])
def job(job_id):
    existing = job_or_404(job_id)
    if not existing: return error("not_found", "job not found", 404)
    if request.method == "GET":
        existing["requirements"] = store().get_requirements(job_id)
        return data(existing)
    payload=body(); return data(store().update_job(job_id, payload.get("name"), payload.get("jd")))

@api.post("/jobs/<int:job_id>/analyze-jd")
def analyze(job_id):
    value=job_or_404(job_id)
    if not value: return error("not_found", "job not found", 404)
    requirements=analyze_jd(value); return data(store().set_requirements(job_id, requirements, "rules"), 201)

@api.post("/jobs/<int:job_id>/workflow-runs")
def start_workflow(job_id):
    if not job_or_404(job_id): return error("not_found", "job not found", 404)
    workflow_id=workflows().start(job_id, trigger="web", background=True)
    return data(store().get_workflow(workflow_id), 202)

@api.get("/workflow-runs/<int:workflow_id>")
def workflow(workflow_id):
    value=store().get_workflow(workflow_id)
    return data(value) if value else error("not_found", "workflow not found", 404)

@api.post("/jobs/<int:job_id>/search-runs")
def search(job_id):
    if not job_or_404(job_id): return error("not_found", "job not found", 404)
    workflow_id=workflows().start(job_id, trigger="search", background=True)
    return data(store().get_workflow(workflow_id), 202)

@api.post("/ingestions")
def ingestion():
    payload=body(); job_id=payload.get("job_id"); source=str(payload.get("source", ""))
    if not isinstance(job_id, int) or not job_or_404(job_id): return error("validation_error", "a valid job_id is required", 422, {"job_id": "invalid"})
    try:
        result=ingest_candidates(store(), job_id, source, payload.get("candidates"), request.headers.get("Idempotency-Key") or payload.get("idempotency_key"))
        return data(result, 200 if result["replayed"] else 201)
    except ValueError as exc: return error("validation_error", str(exc), 422)

@api.get("/ingestions/<int:ingestion_id>")
def get_ingestion(ingestion_id):
    value=store().get_ingestion(ingestion_id); return data(value) if value else error("not_found", "ingestion not found", 404)

@api.get("/jobs/<int:job_id>/candidates")
def candidates(job_id):
    if not job_or_404(job_id): return error("not_found", "job not found", 404)
    return data(store().list_candidates(job_id))

@api.get("/candidates/<int:candidate_id>")
def candidate(candidate_id):
    value=store().get_candidate(candidate_id); return data(value) if value else error("not_found", "candidate not found", 404)

@api.post("/jobs/<int:job_id>/score-runs")
def score(job_id):
    if not job_or_404(job_id): return error("not_found", "job not found", 404)
    try: return data(score_job(store(), job_id), 201)
    except ValueError as exc: return error("precondition_failed", str(exc), 409)

@api.get("/score-runs/<int:run_id>")
def score_run(run_id):
    value=store().get_score_run(run_id); return data(value) if value else error("not_found", "score run not found", 404)

@api.patch("/candidates/<int:candidate_id>/pipeline")
def pipeline(candidate_id):
    payload=body(); stage=str(payload.get("stage", ""))
    if stage not in PIPELINE_STAGES: return error("validation_error", "invalid pipeline stage", 422, {"stage": list(PIPELINE_STAGES)})
    value=store().update_pipeline(candidate_id, stage, str(payload.get("note", "")))
    return data(value) if value else error("not_found", "candidate not found", 404)

@api.post("/candidates/<int:candidate_id>/feedback")
def feedback(candidate_id):
    if not store().get_candidate(candidate_id): return error("not_found", "candidate not found", 404)
    payload=body(); kind=str(payload.get("kind", ""))
    if not kind: return error("validation_error", "kind is required", 422)
    store().add_feedback(candidate_id, kind, str(payload.get("note", ""))); return data({"candidate_id": candidate_id, "kind": kind}, 201)

@api.post("/agent-runs")
def agent_run():
    payload=body(); job_id=payload.get("job_id"); message=str(payload.get("message", "")).strip()
    if not isinstance(job_id, int) or not job_or_404(job_id): return error("validation_error", "a valid job_id is required", 422)
    if not message: return error("validation_error", "message is required", 422)
    return data(agent().run(job_id, message, background=True), 202)

@api.get("/agent-runs/<int:run_id>")
def get_agent(run_id):
    value=agent().refresh(run_id); return data(value) if value else error("not_found", "agent run not found", 404)
