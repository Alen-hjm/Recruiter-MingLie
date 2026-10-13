import json
import sys
from types import SimpleNamespace

import pytest

from app.scrape_state import page_problem, ScrapeNeedsAttention


@pytest.mark.parametrize("url,text,expected", [
    ("about:blank", "", "空白"),
    ("https://h.liepin.com/search", "", "空白"),
    ("https://passport.liepin.com/", "扫码登录", "登录"),
    ("https://h.liepin.com/", "请完成安全验证", "验证"),
    ("https://safe.liepin.com/intercept/ip/captcha/dispatch", "处理中", "验证"),
    ("https://h.liepin.com/search", "简历列表：软件工程师", None),
])
def test_page_states(url, text, expected):
    problem = page_problem(url, text)
    assert (expected in problem) if expected else problem is None


def make_task(user_id):
    from app.models import create_job, create_scrape_task
    job = create_job(user_id, "测试工程师", "Python", "Python 开发经验")
    return job, create_scrape_task(user_id, "Python", "", "liepin", 1, job_id=job)


def test_partial_data_survives_and_resume_deduplicates(user_id, monkeypatch):
    from app.scrape_state import ScrapeNeedsAttention
    from app.models import get_scrape_task, get_resumes_by_job
    from app.services.scraping import run_scrape_task
    job, task = make_task(user_id)
    resume = {"name": "虚构候选人", "res_id_encode": "candidate-a", "full_text": "Python 经历"}
    class Interrupted:
        def __init__(self, cfg): self.cfg = cfg
        async def run(self):
            self.cfg["on_resume"](resume)
            raise ScrapeNeedsAttention("页面空白")
    monkeypatch.setitem(sys.modules, "app.scraper_liepin", SimpleNamespace(LiepinHunterScraper=Interrupted))
    run_scrape_task(task, {}, user_id, job)
    assert get_scrape_task(task)["status"] == "attention"
    assert len(get_resumes_by_job(user_id, job)) == 1
    class Resumed(Interrupted):
        async def run(self):
            self.cfg["on_resume"](resume)
            return [resume]
    sys.modules["app.scraper_liepin"].LiepinHunterScraper = Resumed
    run_scrape_task(task, {}, user_id, job)
    result = get_scrape_task(task)
    assert result["status"] == "done"
    assert result["result_count"] == 1


def test_claim_is_exclusive_and_snapshot_has_no_key(user_id, monkeypatch):
    from app.services import scraping
    from app.models import get_scrape_task
    job, task = make_task(user_id)
    class Thread:
        def __init__(self, **kwargs): pass
        def start(self): pass
    monkeypatch.setattr(scraping.threading, "Thread", Thread)
    runtime = scraping.build_scrape_config({"llm_api_key": "secret-do-not-store"}, "Python", "", 1, user_id=user_id)
    scraping.start_scrape_task(task, runtime, user_id, job)
    assert "secret-do-not-store" not in get_scrape_task(task)["run_config_json"]
    with pytest.raises(ValueError): scraping.start_scrape_task(task, runtime, user_id, job)
    scraping.recover_interrupted_tasks()
    assert get_scrape_task(task)["status"] == "attention"


def test_resume_requires_owner_and_valid_status(logged_in_client, user_id, monkeypatch):
    from app.models import create_user, update_scrape_task
    job, task = make_task(user_id)
    other = create_user("other", "password1234").id
    _, other_task = make_task(other)
    assert logged_in_client.post(f"/api/scrape/{other_task}/resume").status_code == 404
    assert logged_in_client.post(f"/api/scrape/{task}/resume").status_code == 409
    update_scrape_task(task, status="attention", run_config_json=json.dumps({"auto_score": False}))
    calls = []
    from app.web import api_tasks
    monkeypatch.setattr(api_tasks, "start_scrape_task", lambda *args, **kwargs: calls.append((args, kwargs)))
    assert logged_in_client.post(f"/api/scrape/{task}/resume").status_code == 200
    assert calls[0][1]["user_id"] == user_id
    assert calls[0][0][1]["search"]["keyword"] == "Python"


def test_completed_collection_starts_jd_scoring(user_id, monkeypatch):
    from app.models import get_scrape_task
    from app.services import scoring, scraping
    job, task = make_task(user_id)
    class Scraper:
        def __init__(self, cfg): self.cfg = cfg
        async def run(self):
            return [{"name": "虚构候选人", "res_id_encode": "candidate-a", "full_text": "Python 经历"}]
    monkeypatch.setitem(sys.modules, "app.scraper_liepin", SimpleNamespace(LiepinHunterScraper=Scraper))
    calls = []
    def score(*args):
        calls.append(args)
        return 123, None
    monkeypatch.setattr(scoring, "start_score_task", score)
    scraping.run_scrape_task(task, {"auto_score": True}, user_id, job)
    assert calls[0][3] == "Python 开发经验"
    assert get_scrape_task(task)["score_task_id"] == 123
