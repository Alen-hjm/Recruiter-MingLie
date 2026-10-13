"""Assistant entry and run lifecycle; all data and executors are local fakes."""
import asyncio
import copy
from types import SimpleNamespace

import pytest


@pytest.fixture
def runs(monkeypatch):
    from app.web import api_agent as api
    monkeypatch.setattr(api, '_agent_runs', {})
    monkeypatch.setattr(api, '_agent_sessions', {})
    queued = []
    monkeypatch.setattr(api.threading, 'Thread', lambda **kw: SimpleNamespace(start=lambda: queued.append(kw)))
    return api, queued


def test_home_and_login_lead_to_assistant(logged_in_client):
    home = logged_in_client.get('/').get_data(as_text=True)
    assert 'id="assistant-form"' in home
    assert 'id="mission-steps"' in home
    assert 'id="jobResumeChart"' in logged_in_client.get('/overview').get_data(as_text=True)


def test_background_turn_restores_without_duplicate_and_is_user_scoped(logged_in_client, user_id, runs, monkeypatch):
    from app import models
    api, queued = runs
    models.update_settings(user_id, llm_api_key='fake-local-key')
    response = logged_in_client.post('/api/chat/run', json={'message': '找深圳半导体销售总监，10份'})
    assert response.status_code == 202
    run_id = response.get_json()['data']['id']
    assert len(queued) == 1
    assert logged_in_client.post('/api/chat/run', json={'message': '重复提交'}).status_code == 409
    assert logged_in_client.post('/api/chat/clear').status_code == 409
    assert logged_in_client.get('/api/chat/run').get_json()['data']['id'] == run_id
    def fake_agent(uid, message, history, on_progress=None):
        on_progress(phase='searching', label='本地模拟搜寻', completed_phase='understanding')
        return {'success': True, 'summary': '本地模拟完成', 'results': []}
    monkeypatch.setattr(api, '_run_agent_sync', fake_agent)
    queued[0]['target'](*queued[0]['args'])
    result = logged_in_client.get('/api/chat/run').get_json()['data']
    assert result['status'] == 'done'
    assert result['completed_phases'] == ['understanding']
    assert len(logged_in_client.get('/api/chat/history').get_json()['data']) == 2
    models.create_user('another', 'password1234', '另一个测试用户')
    logged_in_client.get('/logout')
    logged_in_client.post('/login', data={'username': 'another', 'password': 'password1234'})
    assert logged_in_client.get('/api/chat/run').get_json()['data'] is None
    assert logged_in_client.get('/api/chat/history').get_json()['data'] == []


def test_unconfigured_run_never_starts(logged_in_client, runs, monkeypatch):
    from app import config
    monkeypatch.setattr(config, 'LLM_API_KEY', '')
    _, queued = runs
    assert logged_in_client.post('/api/chat/run', json={'message': '找人'}).status_code == 400
    assert not queued
    for value in [None, 123, '', 'x' * 12001]:
        assert logged_in_client.post('/api/chat/run', json={'message': value}).status_code == 400


def test_failed_tool_is_attention_and_history_retains_evidence(logged_in_client, user_id, runs, monkeypatch):
    from app import models
    api, queued = runs
    models.update_settings(user_id, llm_api_key='fake-local-key')
    evidence = [{'tool': 'scrape_resumes', 'result': {'success': False, 'error': '本地模拟验证'}}]
    monkeypatch.setattr(api, '_run_agent_sync', lambda *a, **kw: {'success': True, 'summary': '需要验证', 'results': evidence})
    logged_in_client.post('/api/chat/run', json={'message': '模拟测试'})
    queued[0]['target'](*queued[0]['args'])
    assert logged_in_client.get('/api/chat/run').get_json()['data']['status'] == 'attention'
    assert logged_in_client.get('/api/chat/history').get_json()['data'][-1]['details'] == evidence
    assert logged_in_client.post('/api/chat/clear').status_code == 200
    assert logged_in_client.get('/api/chat/run').get_json()['data'] is None


def test_goal_limits_pass_through_and_pending_search_never_starts_scoring(user_id, monkeypatch):
    from app.agent_tools import ToolExecutor
    executor = ToolExecutor(user_id)
    calls = {}
    async def analyze(params):
        return {'success': True, 'data': {'keywords': ['半导体', '销售总监'], 'hard_requirements': ['不得采用的虚构学历要求']}}
    async def create(params):
        calls['job'] = copy.deepcopy(params)
        return {'success': True, 'job_id': 123}
    async def scrape(params):
        calls['scrape'] = copy.deepcopy(params)
        return {'success': False, 'pending': True, 'task_id': 1, 'error': '仍在搜寻'}
    async def score(params):
        pytest.fail('Pending search must not be scored')
    monkeypatch.setattr(executor, '_tool_analyze_jd', analyze)
    monkeypatch.setattr(executor, '_tool_create_job', create)
    monkeypatch.setattr(executor, '_tool_scrape_resumes', scrape)
    monkeypatch.setattr(executor, '_tool_score_resumes', score)
    result = asyncio.run(executor._tool_full_recruit({'job_name': '半导体销售总监', 'city': '深圳', 'max_results': 10}))
    assert not result['success'] and result['pending']
    assert calls['scrape']['city'] == '深圳' and calls['scrape']['max_results'] == 10
    assert '虚构学历' not in calls['job']['job_description']


def test_conversation_evidence_is_not_sent_to_model(user_id, monkeypatch):
    from app import agent
    from app.web.api_agent import _run_agent_sync
    captured = {}
    async def fake(**kw):
        captured.update(kw)
        return {'success': True}
    monkeypatch.setattr(agent, 'run_agent', fake)
    _run_agent_sync(user_id, '继续', [{'role': 'assistant', 'content': '本地测试', 'details': [{'tool': 'fake'}]}])
    assert captured['history'] == [{'role': 'assistant', 'content': '本地测试'}]


def test_planning_failure_never_starts_collection(user_id, monkeypatch):
    from app import agent, models
    models.update_settings(user_id, llm_api_key='fake-local-key')
    async def failed_plan(*args, **kwargs):
        return {'success': False, 'error': '本地模拟模型不可用', 'steps': [], 'reply': '没有启动搜寻'}
    monkeypatch.setattr(agent, 'plan_steps', failed_plan)
    monkeypatch.setattr(agent, 'ToolExecutor', lambda *a, **kw: pytest.fail('Failed planning must not create an executor'))
    result = asyncio.run(agent.run_agent(user_id, '找深圳半导体销售总监'))
    assert not result['success'] and not result['results']


def test_failed_analysis_stops_followup_recommendations(user_id, monkeypatch):
    from app import agent, models
    models.update_settings(user_id, llm_api_key='fake-local-key')
    calls = []
    async def plan(*args, **kwargs):
        return {'steps': [{'tool': 'score_resumes', 'params': {'job_id': 1}},
                          {'tool': 'get_candidates', 'params': {'job_id': 1}}]}
    async def execute(self, tool, params):
        calls.append(tool)
        return {'success': False, 'pending': True, 'error': '本地分析仍在运行'}
    async def summary(*args, **kwargs):
        return '分析尚未完成'
    monkeypatch.setattr(agent, 'plan_steps', plan)
    monkeypatch.setattr(agent.ToolExecutor, 'execute', execute)
    monkeypatch.setattr(agent, 'generate_summary', summary)
    result = asyncio.run(agent.run_agent(user_id, '继续分析'))
    assert not result['success'] and calls == ['score_resumes']
