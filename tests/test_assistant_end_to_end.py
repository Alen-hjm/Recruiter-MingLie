"""One sentence reaches real task services; only browser and model replies are fictional."""
import json
import pytest
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace


@pytest.mark.parametrize("source", ["workspace", "extension"])
def test_one_sentence_runs_collection_scoring_and_recommendation(logged_in_client, user_id, monkeypatch, source):
    from app import models, agent_memory
    from app.services import scoring
    from app.web import api_agent

    goal = '帮我找深圳的半导体销售总监，收集10份简历，无其他要求。'
    calls, runtimes, completed = [], [], threading.Event()

    class LocalModel(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            system = body['messages'][0]['content']
            calls.append(body)
            if '明猎 AI 招聘助手' in system:
                content = json.dumps({'steps': [{'tool': 'full_recruit', 'params': {
                    'job_name': '半导体销售总监', 'job_description': goal,
                    'city': '深圳', 'max_results': 10}, 'description': '搜寻并分析人选'}],
                    'summary': '自动搜寻10份简历'}, ensure_ascii=False)
            elif '擅长分析岗位描述' in system:
                content = json.dumps({'keywords': ['半导体', '销售总监'],
                                      'hard_requirements': [], 'soft_requirements': []})
            elif system == '你是一个简洁的总结助手。':
                content = '本地模拟完成：收集10份虚构简历，分析10份。'
            else:
                content = json.dumps({'total_score': 84, 'pass': True,
                    'summary': '虚构半导体销售经历，需进一步核实', 'scores': {},
                    'hard_requirements_met': []}, ensure_ascii=False)
            payload = json.dumps({'id': 'local-fixture', 'object': 'chat.completion',
                'created': 0, 'model': 'local-fixture', 'choices': [{'index': 0,
                'message': {'role': 'assistant', 'content': content},
                'finish_reason': 'stop'}]}).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

    class FictionalBrowser:
        def __init__(self, runtime):
            self.runtime = runtime
            runtimes.append(runtime)

        async def run(self):
            rows = [{'name': f'虚构人选 {index}', 'title': '半导体销售总监',
                     'location': '深圳', 'full_text': '虚构半导体销售与带队经历',
                     'detail_url': f'https://example.invalid/resume?res_id_encode=fictional-{index}'}
                    for index in range(10)]
            for row in rows:
                self.runtime['on_resume'](row)
            return rows

    monkeypatch.setitem(sys.modules, 'app.scraper_liepin',
                        SimpleNamespace(LiepinHunterScraper=FictionalBrowser))
    monkeypatch.setattr(scoring, '_load_knowledge_context', lambda *args: '')
    monkeypatch.setattr(agent_memory, 'get_relevant_memories', lambda *args: '')
    monkeypatch.setattr(api_agent, '_agent_runs', {})
    monkeypatch.setattr(api_agent, '_agent_sessions', {})
    real_background = api_agent._run_chat_background

    def background(*args):
        try:
            real_background(*args)
        finally:
            completed.set()

    monkeypatch.setattr(api_agent, '_run_chat_background', background)
    server = ThreadingHTTPServer(('127.0.0.1', 0), LocalModel)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        models.update_settings(user_id, llm_api_key='fake-local-key',
            llm_base_url=f'http://127.0.0.1:{server.server_port}/v1', llm_model='local-fixture')
        from app.auth import create_token
        prefix = '/api/extension/chat' if source == 'extension' else '/api/chat'
        headers = {'X-API-Token': create_token(user_id, 'fictional extension')} if source == 'extension' else {}
        response = logged_in_client.post(prefix + '/run', headers=headers, json={'message': goal})
        assert response.status_code == 202
        assert completed.wait(25), 'Local assistant workflow did not finish'
        run = logged_in_client.get(prefix + '/run', headers=headers).get_json()['data']
        assert run['status'] == 'done'
        assert run['scrape']['result_count'] == run['score']['total_count'] == 10
        assert set(run['completed_phases']) == {'understanding', 'searching', 'evaluating', 'recommending'}
        assert runtimes[0]['search']['city'] == '深圳'
        assert runtimes[0]['scraper']['max_results'] == 10
        assert runtimes[0]['scraper']['search_mode'] == 'auto'
        assert not runtimes[0].get('quick_filter')
        job = models.get_job(run['job_id'])
        assert job['job_description'] == goal
        history = logged_in_client.get(prefix + '/history', headers=headers).get_json()['data']
        evidence = history[-1]['details'][0]['result']['data']
        assert evidence['score']['task_id'] == run['score_task_id']
        assert len(evidence['candidates']['recommended']) == 10
        assert evidence['candidates']['recommended'][0]['_summary']
        assert '招聘完成' not in history[-1]['details'][0]['result']['message']
        assert len(calls) == 13  # planning, JD, ten scores, summary
    finally:
        completed.wait(30)
        server.shutdown()
        server.server_close()
        worker.join(5)
