from __future__ import annotations


class RecruiterAgent:
    """A constrained recruiter agent: it only starts the auditable workflow."""
    def __init__(self, store, workflows):
        self.store, self.workflows = store, workflows

    def run(self, job_id: int, message: str, background: bool = True) -> dict:
        workflow_id = self.workflows.start(job_id, trigger="agent", background=background)
        agent_id = self.store.create_agent_run(job_id, message, workflow_id)
        if not background:
            workflow = self.store.get_workflow(workflow_id)
            self._finish(agent_id, workflow)
        return {"agent_run": self.store.get_agent_run(agent_id), "workflow_run": self.store.get_workflow(workflow_id)}

    def refresh(self, agent_id: int) -> dict | None:
        run = self.store.get_agent_run(agent_id)
        if not run: return None
        workflow = self.store.get_workflow(run["workflow_run_id"])
        if run["status"] == "running" and workflow["status"] != "running": self._finish(agent_id, workflow)
        return self.store.get_agent_run(agent_id)

    def _finish(self, agent_id: int, workflow: dict):
        summary = workflow["summary"] or f"招聘工作流状态：{workflow['status']}"
        self.store.finish_agent_run(agent_id, workflow["status"], summary)
