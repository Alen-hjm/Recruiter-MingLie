"""Account-persistent guidance and observed project counts, using fictional data."""
import json

from app import models


def test_guide_remembers_progress_and_does_not_change_setup(logged_in_client, user_id):
    initial = logged_in_client.get('/api/onboarding').get_json()['data']
    assert initial == {'status': 'new', 'step': 0, 'model_configured': False}
    response = logged_in_client.post('/api/onboarding', json={
        'status': 'skipped', 'step': 2, 'user_id': 999})
    assert response.status_code == 200
    assert models.get_onboarding(user_id) == {'status': 'skipped', 'step': 2}
    # A new request/client sees server state; finishing the guide isn't model setup.
    application = logged_in_client.application
    with application.test_client() as new_client:
        new_client.post('/login', data={'username': 'tester', 'password': 'password1234'})
        assert new_client.get('/api/onboarding').get_json()['data']['step'] == 2
        new_client.post('/api/onboarding', json={'status': 'completed', 'step': 3})
    assert not models.get_settings(user_id)['llm_api_key']


def test_guide_state_is_isolated_by_account(logged_in_client, user_id):
    other = models.create_user('other-guide', 'password1234', '虚构用户')
    models.update_onboarding(other.id, 'completed', 3)
    logged_in_client.post('/api/onboarding', json={'status': 'in_progress', 'step': 1})
    assert models.get_onboarding(other.id) == {'status': 'completed', 'step': 3}
    assert models.get_onboarding(user_id) == {'status': 'in_progress', 'step': 1}


def test_guide_rejects_invalid_progress(logged_in_client):
    for payload in [None, [], {'status': 'completed', 'step': True},
                    {'status': 'new', 'step': 0}, {'status': 'skipped', 'step': 4}]:
        response = logged_in_client.post('/api/onboarding', json=payload)
        assert response.status_code == 400
    assert logged_in_client.get('/api/onboarding').get_json()['data']['status'] == 'new'


def test_guide_requires_login(app_client):
    for method in [app_client.get, app_client.post]:
        assert method('/api/onboarding').status_code == 302


def test_overview_counts_deduplicated_resumes_and_latest_analysis(logged_in_client, user_id):
    job = models.create_job(user_id, '虚构销售总监', '销售', '虚构 JD')
    rows = [{'name': '虚构A', 'location': '深圳', 'detail_url': 'https://example.invalid/a'},
            {'name': '虚构B', 'location': '深圳', 'detail_url': 'https://example.invalid/b'}]
    for _ in range(2):
        task = models.create_scrape_task(user_id, '销售', '深圳', 'liepin', 1, job_id=job)
        models.update_scrape_task(task, status='done', result_count=2,
                                  resumes_json=json.dumps(rows))
    score = models.create_score_task(user_id, task, '虚构销售总监', '虚构 JD', '100')
    models.update_score_task(score, status='done', total_count=1, passed_count=1,
        scored_json=json.dumps([{**rows[0], 'score100': {'total_score': 84}}]))
    other = models.create_user('other-project', 'password1234', '其他用户')
    models.create_job(other.id, '不可见项目', '无', '虚构 JD')
    data = logged_in_client.get('/api/overview/projects').get_json()['data']
    assert data['total'] == 1
    assert data['projects'][0] == {'id': job, 'name': '虚构销售总监', 'city': '深圳',
        'status': 'matched', 'collected': 2, 'analyzed': 1, 'matched': 1}
    models.update_scrape_task(task, status='attention')
    assert logged_in_client.get('/api/overview/projects').get_json()['data']['projects'][0]['status'] == 'attention'
