from __future__ import annotations

import threading

from app.repositories import Store
from app.services.ingestion import ingest_candidates
from app.services.jd import analyze_jd
from app.services.scoring import score_job


class WorkflowService:
    def __init__(self, store: Store, search_provider, scrapes=None):
        self.store, self.search_provider, self.scrapes = store, search_provider, scrapes

    def start(self, job_id: int, trigger: str = "web", background: bool = True) -> int:
        workflow_id = self.store.create_workflow(job_id, trigger)
        if background:
            threading.Thread(target=self.execute, args=(workflow_id,), daemon=True).start()
        else:
            self.execute(workflow_id)
        return workflow_id

    def execute(self, workflow_id: int) -> None:
        workflow = self.store.get_workflow(workflow_id)
        if not workflow: return
        job_id = workflow["job_id"]
        try:
            self.store.update_workflow(workflow_id, "running")
            job = self.store.get_job(job_id)
            step = self.store.add_step(workflow_id, "analyze_jd", "running", {"job_id": job_id})
            requirements = analyze_jd(job)
            self.store.set_requirements(job_id, requirements, "rules")
            self.store.finish_step(step, "completed", requirements)
            step = self.store.add_step(workflow_id, "scrape_liepin", "running", requirements)
            if self.search_provider.name == "playwright" and self.scrapes:
                scrape_run_id = self.scrapes.start(job_id, requirements, workflow_id=workflow_id, background=False)
                scrape_run = self.store.get_scrape_run(scrape_run_id)
                if scrape_run["status"] != "completed":
                    self.store.finish_step(step, scrape_run["status"], {"scrape_run_id": scrape_run_id}, scrape_run["error_message"])
                    self.store.update_workflow(workflow_id, scrape_run["status"], scrape_run["error_message"] or "采集任务未完成"); return
                self.store.finish_step(step, "completed", {"scrape_run_id": scrape_run_id, "count": scrape_run["result_count"]})
            else:
                result = self.search_provider.search(requirements)
                self.store.finish_step(step, result.status, {"count": len(result.candidates), "message": result.message})
                if result.status == "waiting_input": self.store.update_workflow(workflow_id, "waiting_input", result.message); return
                step = self.store.add_step(workflow_id, "ingest_candidates", "running", {"count": len(result.candidates)})
                outcome = ingest_candidates(self.store, job_id, self.search_provider.name, result.candidates, f"workflow-{workflow_id}")
                self.store.finish_step(step, "completed", outcome)
            step = self.store.add_step(workflow_id, "score_candidates", "running", {})
            scores = score_job(self.store, job_id)
            self.store.finish_step(step, "completed", {"score_run_id": scores["id"], "count": len(scores["scores"])})
            self.store.update_workflow(workflow_id, "completed", f"已接入并评分 {len(scores['scores'])} 位候选人")
        except Exception as exc:
            self.store.update_workflow(workflow_id, "failed", str(exc))
