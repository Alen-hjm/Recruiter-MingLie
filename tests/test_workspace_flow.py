import json
import sys
from types import SimpleNamespace
import pytest


def test_unanalyzed_candidate_waits_for_review(logged_in_client, user_id):
    from app.models import create_job, create_scrape_task, update_scrape_task
    job = create_job(user_id, '虚构测试岗位', '销售', '销售经历')
    task = create_scrape_task(user_id, '销售', '深圳', 'liepin', 1, job_id=job)
    update_scrape_task(task, status='done', result_count=1,
                      resumes_json=json.dumps([{'name': '虚构人选', 'detail_url': 'https://example.invalid/resume/1'}]))
    result = logged_in_client.get(f'/api/candidates/{job}').get_json()['data']
    assert len(result['pending']) == 1
    assert result['pending'][0]['_has_score'] is False
    assert not result['rejected']


def test_interview_feedback_updates_existing_record_and_candidate_group(logged_in_client, user_id, monkeypatch):
    from app import models
    import app.knowledge_engine as knowledge
    monkeypatch.setattr(knowledge, 'learn_from_feedback', lambda *a, **k: None)
    job = models.create_job(user_id, '反馈测试', '销售', '销售')
    task = models.create_scrape_task(user_id, '销售', '深圳', 'liepin', 1, job_id=job)
    key = 'https://example.invalid/resume/interview'
    models.update_scrape_task(task, status='done', resumes_json=json.dumps([
        {'name': '虚构面试人选', 'detail_url': key}]))
    logged_in_client.post('/api/candidate/action', json={
        'action': 'interview', 'job_id': job, 'candidate_name': '虚构面试人选', 'resume_key': key})
    feedback = models.get_interview_feedbacks(user_id, job)[0]
    for stage, group in [('interview', 'interviewed'), ('submitted', 'interviewed'), ('rejected', 'rejected')]:
        response = logged_in_client.post('/api/pipeline/update', json={
            'job_id': job, 'candidate_name': '虚构面试人选', 'feedback_id': feedback['id'],
            'stage': stage, 'reason': '虚构面试记录'})
        assert response.status_code == 200
        assert len(models.get_interview_feedbacks(user_id, job)) == 1
        assert len(logged_in_client.get(f'/api/candidates/{job}').get_json()['data'][group]) == 1


@pytest.mark.parametrize('limit', [0, 51, 'invalid'])
def test_invalid_collection_limit_is_rejected(logged_in_client, user_id, limit):
    from app.models import create_job
    job = create_job(user_id, '测试岗位', '销售', '销售经历')
    response = logged_in_client.post('/api/scrape', json={'job_id': job, 'max_results': limit})
    assert response.status_code == 400


def test_city_and_limit_reach_collection_runtime(logged_in_client, user_id, monkeypatch):
    from app.models import create_job, get_scrape_task, update_scrape_task
    from app.web import api_tasks
    job = create_job(user_id, '销售总监', '半导体 销售总监', '深圳')
    calls = []
    monkeypatch.setattr(api_tasks, 'start_scrape_task', lambda task, runtime, **kw: calls.append(runtime))
    response = logged_in_client.post('/api/scrape', json={'job_id': job, 'city': '深圳', 'max_results': 10})
    assert response.status_code == 200
    assert calls[0]['scraper']['max_results'] == 10
    assert calls[0]['search']['city'] == '深圳'
    task = response.get_json()['task_id']
    update_scrape_task(task, status='attention', run_config_json=json.dumps(calls[0]))
    assert logged_in_client.post(f'/api/scrape/{task}/resume').status_code == 200
    assert calls[1]['scraper']['max_results'] == 10


def test_resuming_only_collects_remaining_count(user_id, monkeypatch):
    from app.models import create_job, create_scrape_task, update_scrape_task
    from app.services.scraping import run_scrape_task
    job = create_job(user_id, '测试岗位', '销售', '销售')
    task = create_scrape_task(user_id, '销售', '深圳', 'liepin', 1, job_id=job)
    update_scrape_task(task, status='attention', resumes_json=json.dumps([{'name': '虚构 A'}, {'name': '虚构 B'}]))
    captures = []
    class Scraper:
        def __init__(self, runtime): captures.append(runtime)
        async def run(self): return []
    monkeypatch.setitem(sys.modules, 'app.scraper_liepin', SimpleNamespace(LiepinHunterScraper=Scraper))
    run_scrape_task(task, {'scraper': {'max_results': 10}}, user_id, job)
    assert captures[0]['scraper']['max_results'] == 8
