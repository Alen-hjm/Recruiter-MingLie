"""Headless side-panel regression checks with fictional data and mocked Chrome APIs.
Run: python scripts/verify_extension.py (requires playwright and Chromium).
"""
import json
import tempfile
from pathlib import Path
from playwright.sync_api import sync_playwright, expect
ROOT = Path(__file__).resolve().parents[1]
MOCK = r'''(() => {
const listeners = [], stored = {apiToken:"ml_fixture",serverUrl:"http://127.0.0.1:5000",selectedJobId:1};
window.fixture={stored,posted:[],history:[],run:null,failSubmit:false,pending:false,otherPage:false};
window.chrome={storage:{local:{get:async keys=>Object.fromEntries((Array.isArray(keys)?keys:[keys]).map(k=>[k,stored[k]])),set:async values=>Object.assign(stored,values),remove:async keys=>{for(const k of Array.isArray(keys)?keys:[keys])delete stored[k];}},onChanged:{addListener:fn=>listeners.push(fn)}},permissions:{request:async()=>true},tabs:{query:async()=>[{id:1,url:fixture.otherPage?"https://example.invalid/":"https://h.liepin.com/resume?res_id_encode=fictional"}],create:async()=>{},sendMessage:async(id,msg)=>msg.action==="get_page_info"?{isDetailPage:true}:{success:true,name:"虚构人选",full_text:"仅用于测试的虚构简历内容",detail_url:"https://h.liepin.com/resume?res_id_encode=fictional"},onActivated:{addListener:()=>{}},onUpdated:{addListener:()=>{}}}};
window.fetch=async(url,options={})=>{
 const path=new URL(url).pathname.replace("/api/extension","");
 if(!options.headers["X-API-Token"])throw Error("Missing token");
 let data={success:true};
 if(path==="/jobs")data.data=[{id:1,name:"虚构半导体销售总监"},{id:2,name:"虚构第二岗位"}];
 else if(path==="/chat/history")data.data=fixture.history;
 else if(path==="/chat/run"){
  if(options.method==="POST") {const body=JSON.parse(options.body);fixture.posted.push(body);fixture.history.push({role:"user",content:body.message});fixture.run={id:"fixture",status:"running",phase:"searching",label:"正在搜寻虚构候选人",completed_phases:["understanding"]};}
  data.data=fixture.run;
 } else if(path.startsWith("/resumes/")) data.data=[{name:"虚构人选 <img src=x onerror=alert(1)>",location:"深圳",education:"本科",score100:{total_score:86,recommend_reason:"<script>bad</script> **虚构推荐**"},skill_tags:["半导体"],detail_url:"javascript:alert(1)"},{name:"待分析人选",location:"上海"},{name:"零分人选",score100:{total_score:0}}];
 else if(path.startsWith("/pipeline/")&&options.method!=="POST")data={success:true,stages:{recommended:[{id:1,candidate_name:"虚构人选",ai_score:86}]}};
 else if(path==="/submit_batch"){fixture.posted.push(JSON.parse(options.body));if(fixture.failSubmit)throw Error("Offline");data.score_task_id=11;}
 else if(path==="/chat/clear"){fixture.history=[];fixture.run=null;}
 return {ok:true,status:200,json:async()=>structuredClone(data)};
};
})();'''

def main():
    errors=[]
    with sync_playwright() as p:
        browser=p.chromium.launch(headless=True)
        page=browser.new_page(viewport={"width":400,"height":900})
        page.add_init_script(MOCK)
        page.on("pageerror",lambda error:errors.append(str(error)))
        page.goto((ROOT / "chrome-extension/sidepanel.html").as_uri())
        expect(page.locator("#connectionState")).to_have_text("工作台已连接")
        expect(page.locator("#onboarding")).to_be_visible()
        page.locator("#dismissGuide").click()
        page.locator("[data-prompt]").first.click()
        assert page.locator("#chatInput").input_value().startswith("帮我找深圳")
        page.locator("[data-panel=resumes]").click()
        expect(page.locator(".panel.active .candidate")).to_have_count(3)
        assert page.locator(".candidate img,.candidate script,.candidate a").count()==0
        page.locator("#selFilter").select_option("pending")
        expect(page.locator(".panel.active .candidate")).to_have_count(1)
        expect(page.locator(".candidate-name")).to_have_text("待分析人选")
        page.locator("#selFilter").select_option("low")
        expect(page.locator(".candidate-name")).to_have_text("零分人选")
        page.locator("#selFilter").select_option("")
        page.locator("#candidateSearch").fill("深圳")
        expect(page.locator(".panel.active .candidate")).to_have_count(1)
        page.locator("[data-panel=chat]").click()
        page.locator("#btnSend").click()
        expect(page.locator("#runLabel")).to_have_text("正在搜寻虚构候选人")
        expect(page.locator("#btnSend")).to_be_disabled()
        page.locator("[data-panel=settings]").click()
        expect(page.locator("#inputServer")).to_have_value("http://127.0.0.1:5000")
        page.locator("[data-panel=chat]").click()
        page.evaluate("fixture.run.status='done'; fixture.run.label='本轮处理完成';fixture.history.push({role:'assistant',content:'**已完成** <img src=x onerror=alert(1)>'})")
        expect(page.locator("#runLabel")).to_have_text("本轮处理完成",timeout=8000)
        assert page.locator("#chatMessages img").count()==0
        expect(page.locator("#btnSend")).to_be_enabled()
        page.locator("#clearChat").click()
        expect(page.locator("#confirmDialog")).to_be_visible()
        page.locator("#cancelConfirm").click()
        expect(page.locator(".msg")).to_have_count(2)
        page.evaluate("fixture.failSubmit=true")
        page.locator("#analyzePage").click()
        expect(page.locator("#collectBar")).to_be_visible()
        expect(page.locator("#retrySubmit")).to_be_enabled()
        assert page.evaluate("fixture.stored.collectedJobId")==1
        page.evaluate("fixture.failSubmit=false")
        page.locator("#retrySubmit").click()
        expect(page.locator("#collectBar")).to_be_hidden()
        page.locator("[data-panel=pipeline]").click()
        page.locator("[data-action=remove]").click()
        page.locator("#cancelConfirm").click()
        expect(page.locator(".panel.active .candidate")).to_have_count(1)
        page.locator("[data-panel=chat]").click()
        page.locator("#clearChat").click();page.locator("#acceptConfirm").click()
        expect(page.locator("#welcome")).to_be_visible()
        output=ROOT/"data/extension-preview";output.mkdir(parents=True,exist_ok=True)
        page.locator("#toast").wait_for(state="hidden");page.screenshot(path=str(output/"assistant.png"),animations="disabled")
        for width in [340,400,520]:
            page.set_viewport_size({"width":width,"height":800})
            assert page.evaluate("document.documentElement.scrollWidth<=innerWidth"),width
            for tab in ["resumes","pipeline","settings","chat"]:
                page.locator(f"[data-panel={tab}]").click()
                assert page.evaluate("document.documentElement.scrollWidth<=innerWidth"),(width,tab)
        page.set_viewport_size({"width":400,"height":900})
        page.locator("[data-panel=settings]").click();page.screenshot(path=str(output/"settings.png"),animations="disabled")
        assert not errors,errors
        browser.close()
        # Load the actual MV3 package in a disposable profile. Every HTTP request
        # is intercepted; neither a real workspace nor Liepin is contacted.
        with tempfile.TemporaryDirectory(prefix="minglie-extension-",ignore_cleanup_errors=True) as profile:
            context=p.chromium.launch_persistent_context(profile,headless=True,channel="chromium",
                args=[f"--disable-extensions-except={ROOT / 'chrome-extension'}",f"--load-extension={ROOT / 'chrome-extension'}"])
            try:
                observed=[]
                def intercept(route):
                    request=route.request
                    if request.url.startswith("http://127.0.0.1:5000/api/extension/"):
                        observed.append(request.headers.get("x-api-token"))
                        endpoint=request.url.split("/api/extension/")[1]
                        payload={"success":True,"data":[{"id":1,"name":"虚构岗位"}]} if endpoint=="jobs" else {"success":True,"data":[] if endpoint=="chat/history" else None}
                        route.fulfill(content_type="application/json",body=json.dumps(payload))
                    elif request.url.startswith("https://h.liepin.com/"):
                        route.fulfill(content_type="text/html; charset=utf-8",body='<div class="resume-content"><h1>虚构候选人</h1>'+('虚构半导体销售经历，仅供本地验收。'*25)+'</div>')
                    elif request.url.startswith("chrome-extension://"):
                        route.continue_()
                    else:
                        route.abort()
                context.route("**/*",intercept)
                worker=context.service_workers[0] if context.service_workers else context.wait_for_event("serviceworker")
                extension_id=worker.url.split("/")[2]
                assert worker.evaluate("chrome.runtime.getManifest().version")=="1.1.0"
                worker.evaluate("chrome.storage.local.set({apiToken:'ml_isolated_fixture',selectedJobId:1,serverUrl:'http://127.0.0.1:5000'})")
                extension_page=context.new_page()
                extension_page.goto(f"chrome-extension://{extension_id}/sidepanel.html")
                expect(extension_page.locator("#connectionState")).to_have_text("工作台已连接")
                expect(extension_page.locator("#inputApiToken")).to_have_value("ml_isolated_fixture")
                extension_page.locator("[data-panel=settings]").click()
                expect(extension_page.locator("#inputServer")).to_be_visible()
                fictional_page=context.new_page()
                fictional_page.goto("https://h.liepin.com/resume?res_id_encode=fictional-only")
                fictional_page.wait_for_load_state("load")
                info=worker.evaluate("""async () => {
                    const tabs=await chrome.tabs.query({url:'https://h.liepin.com/*'});
                    return chrome.tabs.sendMessage(tabs[0].id,{action:'get_page_info'});
                }""")
                assert info['isDetailPage']
                resume=worker.evaluate("""async () => {
                    const tabs=await chrome.tabs.query({url:'https://h.liepin.com/*'});
                    return chrome.tabs.sendMessage(tabs[0].id,{action:'scrape_resume'});
                }""")
                assert resume['success'] and '虚构' in resume['full_text']
                assert observed and all(token=="ml_isolated_fixture" for token in observed)
            finally:
                context.close()
    print("PASS: token restore, onboarding, safe rendering, filters, background run, panel switching, confirmation, failed-submit retry, narrow sidebar layouts, actual MV3 load and content-script messaging")

if __name__=="__main__":main()


