from __future__ import annotations

import json
from typing import Any

from app.db import connect


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _row(row):
    return dict(row) if row else None


class Store:
    def __init__(self, database):
        self.database = database

    def _conn(self):
        return connect(self.database)

    def create_job(self, name: str, jd: str) -> dict:
        with self._conn() as c:
            cur = c.execute("INSERT INTO jobs(name,jd) VALUES (?,?)", (name, jd))
            return self.get_job(cur.lastrowid, c)

    def list_jobs(self) -> list[dict]:
        with self._conn() as c:
            return [_row(x) for x in c.execute("SELECT * FROM jobs ORDER BY updated_at DESC, id DESC")]

    def get_job(self, job_id: int, conn=None) -> dict | None:
        close = conn is None
        conn = conn or self._conn()
        try:
            return _row(conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone())
        finally:
            if close: conn.close()

    def update_job(self, job_id: int, name: str | None = None, jd: str | None = None) -> dict | None:
        with self._conn() as c:
            old = self.get_job(job_id, c)
            if not old: return None
            c.execute("UPDATE jobs SET name=?, jd=?, updated_at=CURRENT_TIMESTAMP WHERE id=?", (name if name is not None else old['name'], jd if jd is not None else old['jd'], job_id))
            return self.get_job(job_id, c)

    def set_requirements(self, job_id: int, requirements: dict, provider: str) -> dict:
        with self._conn() as c:
            c.execute("INSERT INTO job_requirements(job_id,requirements_json,provider) VALUES(?,?,?) ON CONFLICT(job_id) DO UPDATE SET requirements_json=excluded.requirements_json,provider=excluded.provider,updated_at=CURRENT_TIMESTAMP", (job_id, _json(requirements), provider))
            return self.get_requirements(job_id, c)

    def get_requirements(self, job_id: int, conn=None) -> dict | None:
        close = conn is None; conn = conn or self._conn()
        try:
            row = _row(conn.execute("SELECT * FROM job_requirements WHERE job_id=?", (job_id,)).fetchone())
            if row: row['requirements'] = json.loads(row.pop('requirements_json'))
            return row
        finally:
            if close: conn.close()

    def create_ingestion(self, job_id: int, source: str, key: str | None) -> dict:
        with self._conn() as c:
            cur = c.execute("INSERT INTO ingestions(job_id,source,idempotency_key,status) VALUES(?,?,?,'running')", (job_id, source, key))
            return self.get_ingestion(cur.lastrowid, c)

    def get_ingestion_by_key(self, key: str) -> dict | None:
        with self._conn() as c:
            row = _row(c.execute("SELECT * FROM ingestions WHERE idempotency_key=?", (key,)).fetchone())
            if row: row['results'] = json.loads(row.pop('result_json'))
            return row

    def ingest(self, ingestion_id: int, job_id: int, source: str, candidates: list[dict]) -> dict:
        outcomes = []
        with self._conn() as c:
            for item in candidates:
                external_id, name = str(item.get('external_id', '')).strip(), str(item.get('name', '')).strip()
                if not external_id or not name:
                    outcomes.append({'external_id': external_id, 'status': 'rejected', 'reason': 'external_id and name are required'}); continue
                fields = (name, str(item.get('headline', '')), str(item.get('location', '')), item.get('experience_years'), str(item.get('education', '')), _json(item.get('skills', [])), str(item.get('summary', '')), _json(item))
                existing = c.execute("SELECT * FROM candidates WHERE job_id=? AND source=? AND external_id=?", (job_id, source, external_id)).fetchone()
                if existing:
                    old_payload = existing['raw_json']
                    if old_payload == fields[-1]:
                        status, candidate_id = 'duplicate', existing['id']
                    else:
                        c.execute("UPDATE candidates SET name=?,headline=?,location=?,experience_years=?,education=?,skills_json=?,summary=?,raw_json=?,updated_at=CURRENT_TIMESTAMP WHERE id=?", (*fields, existing['id']))
                        status, candidate_id = 'updated', existing['id']
                else:
                    cur = c.execute("INSERT INTO candidates(job_id,source,external_id,name,headline,location,experience_years,education,skills_json,summary,raw_json) VALUES(?,?,?,?,?,?,?,?,?,?,?)", (job_id, source, external_id, *fields))
                    status, candidate_id = 'created', cur.lastrowid
                c.execute("INSERT INTO candidate_sources(candidate_id,ingestion_id,payload_json) VALUES(?,?,?)", (candidate_id, ingestion_id, fields[-1]))
                outcomes.append({'external_id': external_id, 'candidate_id': candidate_id, 'status': status})
            counts = {s: sum(x['status'] == s for x in outcomes) for s in ('created','updated','duplicate','rejected')}
            c.execute("UPDATE ingestions SET status='completed',received_count=?,created_count=?,updated_count=?,duplicate_count=?,rejected_count=?,result_json=?,completed_at=CURRENT_TIMESTAMP WHERE id=?", (len(candidates),counts['created'],counts['updated'],counts['duplicate'],counts['rejected'],_json(outcomes),ingestion_id))
        return self.get_ingestion(ingestion_id)

    def get_ingestion(self, ingestion_id: int, conn=None) -> dict | None:
        close = conn is None; conn = conn or self._conn()
        try:
            row = _row(conn.execute("SELECT * FROM ingestions WHERE id=?", (ingestion_id,)).fetchone())
            if row: row['results'] = json.loads(row.pop('result_json'))
            return row
        finally:
            if close: conn.close()

    def list_candidates(self, job_id: int) -> list[dict]:
        sql = """SELECT c.*, cs.total_score,cs.recommendation,cs.matched_json,cs.gaps_json,cs.evidence_json FROM candidates c LEFT JOIN candidate_scores cs ON cs.id=(SELECT id FROM candidate_scores WHERE candidate_id=c.id ORDER BY id DESC LIMIT 1) WHERE c.job_id=? ORDER BY COALESCE(cs.total_score,-1) DESC,c.updated_at DESC"""
        with self._conn() as c:
            rows = [_row(x) for x in c.execute(sql, (job_id,))]
        for row in rows:
            for key in ('skills_json','matched_json','gaps_json','evidence_json'):
                row[key[:-5] if key.endswith('_json') else key] = json.loads(row.pop(key) or '[]')
        return rows

    def get_candidate(self, candidate_id: int) -> dict | None:
        with self._conn() as c:
            rows = self.list_candidates(c.execute("SELECT job_id FROM candidates WHERE id=?", (candidate_id,)).fetchone()['job_id']) if c.execute("SELECT 1 FROM candidates WHERE id=?", (candidate_id,)).fetchone() else []
            return next((x for x in rows if x['id'] == candidate_id), None)

    def create_score_run(self, job_id: int, provider: str, count: int) -> int:
        with self._conn() as c:
            return c.execute("INSERT INTO score_runs(job_id,status,provider,candidate_count) VALUES(?,'running',?,?)", (job_id,provider,count)).lastrowid

    def save_score(self, run_id: int, candidate_id: int, score: dict, provider: str):
        with self._conn() as c:
            c.execute("INSERT INTO candidate_scores(score_run_id,candidate_id,total_score,recommendation,matched_json,gaps_json,evidence_json,provider) VALUES(?,?,?,?,?,?,?,?)", (run_id,candidate_id,score['total_score'],score['recommendation'],_json(score['matched']),_json(score['gaps']),_json(score['evidence']),provider))

    def finish_score_run(self, run_id: int, status='completed'):
        with self._conn() as c: c.execute("UPDATE score_runs SET status=?,completed_at=CURRENT_TIMESTAMP WHERE id=?", (status,run_id))

    def get_score_run(self, run_id: int) -> dict | None:
        with self._conn() as c:
            row = _row(c.execute("SELECT * FROM score_runs WHERE id=?", (run_id,)).fetchone())
            if row: row['scores'] = [_row(x) for x in c.execute("SELECT * FROM candidate_scores WHERE score_run_id=? ORDER BY total_score DESC", (run_id,))]
            return row

    def list_score_runs(self, job_id: int | None = None) -> list[dict]:
        query = "SELECT * FROM score_runs"; values = ()
        if job_id is not None: query += " WHERE job_id=?"; values = (job_id,)
        query += " ORDER BY id DESC"
        with self._conn() as c: return [_row(x) for x in c.execute(query, values)]

    def create_scrape_run(self, job_id: int, workflow_id: int | None, keyword: str, city: str, max_pages: int, browser_profile: str) -> int:
        with self._conn() as c:
            return c.execute("INSERT INTO scrape_runs(job_id,workflow_run_id,keyword,city,max_pages,browser_profile,status,started_at) VALUES(?,?,?,?,?,?, 'queued', CURRENT_TIMESTAMP)", (job_id, workflow_id, keyword, city, max_pages, browser_profile)).lastrowid

    def update_scrape_run(self, run_id: int, status: str, result_count: int | None = None, error_message: str = ''):
        fields, values = ["status=?"], [status]
        if result_count is not None: fields.append("result_count=?"); values.append(result_count)
        if error_message: fields.append("error_message=?"); values.append(error_message)
        if status in ("completed", "failed", "cancelled"): fields.append("completed_at=CURRENT_TIMESTAMP")
        values.append(run_id)
        with self._conn() as c: c.execute(f"UPDATE scrape_runs SET {', '.join(fields)} WHERE id=?", values)

    def add_scrape_log(self, run_id: int, message: str, level: str = 'info'):
        with self._conn() as c: c.execute("INSERT INTO scrape_logs(scrape_run_id,level,message) VALUES(?,?,?)", (run_id, level, message[:2000]))

    def get_scrape_run(self, run_id: int) -> dict | None:
        with self._conn() as c: return _row(c.execute("SELECT * FROM scrape_runs WHERE id=?", (run_id,)).fetchone())

    def list_scrape_runs(self, job_id: int | None = None, limit: int = 30) -> list[dict]:
        query = "SELECT s.*,j.name AS job_name FROM scrape_runs s JOIN jobs j ON j.id=s.job_id"; values=[]
        if job_id is not None: query += " WHERE s.job_id=?"; values.append(job_id)
        query += " ORDER BY s.id DESC LIMIT ?"; values.append(limit)
        with self._conn() as c: return [_row(x) for x in c.execute(query, values)]

    def get_scrape_logs(self, run_id: int) -> list[dict]:
        with self._conn() as c: return [_row(x) for x in c.execute("SELECT * FROM scrape_logs WHERE scrape_run_id=? ORDER BY id", (run_id,))]

    def pipeline(self, job_id: int) -> dict:
        stages = {stage: [] for stage in ("new", "contacting", "contacted", "invited", "interviewing", "rejected", "hired")}
        for candidate in self.list_candidates(job_id): stages.setdefault(candidate['stage'], []).append(candidate)
        return {"stages": stages, "summary": {**{key: len(value) for key, value in stages.items()}, "total": sum(map(len, stages.values()))}}

    def stats(self) -> dict:
        with self._conn() as c:
            jobs = c.execute("SELECT COUNT(*) AS n FROM jobs").fetchone()['n']; candidates=c.execute("SELECT COUNT(*) AS n FROM candidates").fetchone()['n']; scored=c.execute("SELECT COUNT(*) AS n FROM candidate_scores").fetchone()['n']; active=c.execute("SELECT COUNT(*) AS n FROM scrape_runs WHERE status IN ('queued','running','waiting_login')").fetchone()['n']
            return {"jobs":jobs,"candidates":candidates,"scored":scored,"active_scrapes":active}

    def create_workflow(self, job_id: int, trigger: str) -> int:
        with self._conn() as c: return c.execute("INSERT INTO workflow_runs(job_id,status,trigger) VALUES(?,'queued',?)", (job_id,trigger)).lastrowid

    def update_workflow(self, workflow_id: int, status: str, summary: str = ''):
        with self._conn() as c: c.execute("UPDATE workflow_runs SET status=?,summary=?,updated_at=CURRENT_TIMESTAMP WHERE id=?", (status,summary,workflow_id))

    def get_workflow(self, workflow_id: int) -> dict | None:
        with self._conn() as c:
            row = _row(c.execute("SELECT * FROM workflow_runs WHERE id=?", (workflow_id,)).fetchone())
            if row: row['steps'] = [_row(x) for x in c.execute("SELECT * FROM workflow_steps WHERE workflow_run_id=? ORDER BY id", (workflow_id,))]
            return row

    def add_step(self, workflow_id: int, tool: str, status: str, payload: dict) -> int:
        with self._conn() as c: return c.execute("INSERT INTO workflow_steps(workflow_run_id,tool_name,status,input_json) VALUES(?,?,?,?)", (workflow_id,tool,status,_json(payload))).lastrowid

    def finish_step(self, step_id: int, status: str, output: dict | None = None, error: str = ''):
        with self._conn() as c: c.execute("UPDATE workflow_steps SET status=?,output_json=?,error=?,completed_at=CURRENT_TIMESTAMP WHERE id=?", (status,_json(output or {}),error,step_id))

    def create_search_run(self, job_id: int, workflow_id: int, provider: str, criteria: dict) -> int:
        with self._conn() as c: return c.execute("INSERT INTO search_runs(job_id,workflow_run_id,provider,criteria_json,status) VALUES(?,?,?,?, 'running')", (job_id,workflow_id,provider,_json(criteria))).lastrowid

    def finish_search_run(self, run_id: int, status: str, count: int, message: str):
        with self._conn() as c: c.execute("UPDATE search_runs SET status=?,received_count=?,message=?,updated_at=CURRENT_TIMESTAMP WHERE id=?", (status,count,message,run_id))

    def update_pipeline(self, candidate_id: int, stage: str, note: str) -> dict | None:
        with self._conn() as c:
            c.execute("UPDATE candidates SET stage=?,updated_at=CURRENT_TIMESTAMP WHERE id=?", (stage,candidate_id)); c.execute("INSERT INTO pipeline_events(candidate_id,stage,note) VALUES(?,?,?)", (candidate_id,stage,note))
        return self.get_candidate(candidate_id)

    def add_feedback(self, candidate_id: int, kind: str, note: str):
        with self._conn() as c: c.execute("INSERT INTO feedback(candidate_id,kind,note) VALUES(?,?,?)", (candidate_id,kind,note))

    def create_agent_run(self, job_id: int, message: str, workflow_id: int) -> int:
        with self._conn() as c:
            rid=c.execute("INSERT INTO agent_runs(job_id,message,status,workflow_run_id) VALUES(? ,?,'running',?)", (job_id,message,workflow_id)).lastrowid
            c.execute("INSERT INTO agent_messages(agent_run_id,role,content) VALUES(?,?,?)", (rid,'user',message)); return rid

    def finish_agent_run(self, run_id: int, status: str, summary: str):
        with self._conn() as c:
            c.execute("UPDATE agent_runs SET status=?,summary=?,updated_at=CURRENT_TIMESTAMP WHERE id=?", (status,summary,run_id)); c.execute("INSERT INTO agent_messages(agent_run_id,role,content) VALUES(?,?,?)", (run_id,'assistant',summary))

    def get_agent_run(self, run_id: int) -> dict | None:
        with self._conn() as c:
            row=_row(c.execute("SELECT * FROM agent_runs WHERE id=?", (run_id,)).fetchone())
            if row: row['messages']=[_row(x) for x in c.execute("SELECT role,content,created_at FROM agent_messages WHERE agent_run_id=? ORDER BY id", (run_id,))]
            return row
