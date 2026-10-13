"""Real HTTP/SDK + SQLite workflow; only collection and model content are fixtures."""
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace


def test_collection_jd_http_scoring_and_repeat_dedup(user_id, monkeypatch):
    from app import models
    from app.services import scoring, scraping

    jd = "虚构岗位：Python 后端开发，必须掌握 Python 和 SQL。"
    job = models.create_job(user_id, "虚构集成验收岗位", "Python", jd)
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args): pass
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            requests.append((self.path, body))
            prompt = body["messages"][-1]["content"]
            score = 82 if "fixture_python_sql" in prompt else 35
            content = json.dumps({
                "total_score": score, "pass": score >= 60,
                "hard_requirements_met": [{"requirement": "Python 和 SQL", "met": score >= 60}],
                "summary": "固定测试响应，不代表真实模型评估", "scores": {},
            }, ensure_ascii=False)
            payload = json.dumps({"id": "local-fixture", "object": "chat.completion",
                "created": 0, "model": "local-fixture",
                "choices": [{"index": 0, "message": {"role": "assistant", "content": content},
                             "finish_reason": "stop"}]}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    score_threads = []
    real_start = scoring.start_score_task
    def start(*args, **kwargs):
        task, thread = real_start(*args, **kwargs)
        score_threads.append(thread)
        return task, thread
    monkeypatch.setattr(scoring, "start_score_task", start)
    monkeypatch.setattr(scoring, "_load_knowledge_context", lambda *args: "")
    # Both records are fictional; no real resumes or third-party model endpoints.
    rows = [
        {"name": "虚构甲", "detail_url": "https://example.test/resume?res_id_encode=fixture-a",
         "full_text": "fixture_python_sql：Python API 与 SQL 项目经历"},
        {"name": "虚构乙", "detail_url": "https://example.test/resume?res_id_encode=fixture-b",
         "full_text": "fixture_other：测试用文档整理经历"},
    ]
    class FixtureScraper:
        def __init__(self, config): self.config = config
        async def run(self):
            for row in rows:
                self.config["on_resume"](dict(row))
            return [dict(row) for row in rows]
    monkeypatch.setitem(sys.modules, "app.scraper_liepin", SimpleNamespace(LiepinHunterScraper=FixtureScraper))

    try:
        models.update_settings(user_id, llm_api_key="local-fixture-key",
            llm_base_url=f"http://127.0.0.1:{server.server_port}/v1", llm_model="local-fixture")
        first = models.create_scrape_task(user_id, "Python", "", "liepin", 1, job_id=job)
        scraping.run_scrape_task(first, {"auto_score": True}, user_id, job)
        assert len(score_threads) == 1
        score_threads[0].join(15)
        assert not score_threads[0].is_alive()
        collected = models.get_scrape_task(first)
        assert collected["status"] == "done" and collected["result_count"] == 2
        scored = models.get_score_task(collected["score_task_id"])
        assert scored["status"] == "done"
        results = json.loads(scored["scored_json"])
        assert sorted(r["score100"]["total_score"] for r in results) == [35, 82]
        assert scored["passed_count"] == 1
        assert len(models.get_resumes_by_job(user_id, job)) == 2  # Low score retained.
        assert len(requests) == 2
        assert all(path == "/v1/chat/completions" and jd in body["messages"][-1]["content"]
                   for path, body in requests)
        conn = models.get_db()
        try:
            assert conn.execute("SELECT COUNT(*) FROM interview_feedback WHERE job_id=?", (job,)).fetchone()[0] == 1
        finally:
            conn.close()
        second = models.create_scrape_task(user_id, "Python", "", "liepin", 1, job_id=job)
        scraping.run_scrape_task(second, {"auto_score": True}, user_id, job)
        assert models.get_scrape_task(second)["result_count"] == 0
        assert len(score_threads) == 1 and len(requests) == 2
        assert len(models.get_resumes_by_job(user_id, job)) == 2
    finally:
        for thread in score_threads:
            thread.join(15)
        server.shutdown()
        server.server_close()
        worker.join(5)
