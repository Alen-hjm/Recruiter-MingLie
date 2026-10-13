/* The assistant owns the flow. The UI only displays confirmed server state. */
(() => {
  const space=document.getElementById('assistant-space'),feed=document.getElementById('conversation-feed'),input=document.getElementById('chat-input'),send=document.getElementById('btn-send'),clear=document.getElementById('clear-chat');
  let busy=false,ready=false,pollTimer=null,currentRun=null,lastHistory='',modelConfigured=false;
  const draftKey='assistant-draft';
  const labels={planning:'理解需求',understanding:'理解需求',searching:'搜寻人才',evaluating:'分析匹配',recommending:'整理推荐',summarizing:'整理结果',working:'处理需求',finished:'本轮结束'};
  const toolLabels={full_recruit:'搜寻并分析人选',analyze_jd:'梳理岗位需求',create_job:'建立招聘项目',scrape_resumes:'搜寻人才',score_resumes:'分析匹配',get_candidates:'整理人选',get_job_list:'读取招聘进展',update_candidate_status:'记录招聘进展',get_pipeline:'读取候选人进展',save_job_to_obsidian:'保存岗位资料',sync_pipeline_to_obsidian:'同步招聘进展',search_knowledge:'查阅招聘资料'};
  function rememberDraft(){saveState(draftKey,input.value);input.style.height='auto';input.style.height=Math.min(230,Math.max(80,input.scrollHeight))+'px';}
  function usePrompt(text){input.value=text;rememberDraft();input.focus();window.MinglieMotion?.pulse(document.getElementById('assistant-form'));document.getElementById('assistant-form').scrollIntoView({block:'nearest',behavior:matchMedia('(prefers-reduced-motion:reduce)').matches?'auto':'smooth'});}
  function conversationMode(on){space.classList.toggle('has-conversation',on);feed.hidden=!on;}
  function setBusy(value){busy=value;space.classList.toggle('is-working',value);send.disabled=value||!ready;clear.disabled=value||!ready;send.querySelector('span').textContent=value?'正在推进':'交给明猎';input.setAttribute('aria-busy',String(value));}
  function resultContext(rows){
    let jobId=null,scoreId=null,people=[];
    for(const row of rows||[]){const result=row.result||{},data=result.data||{};
      jobId=result.job_id||data.job_id||jobId;
      scoreId=data.score?.task_id||(row.tool==='score_resumes'?result.task_id:null)||scoreId;
      const candidates=result.recommended||data.candidates?.recommended;
      if(Array.isArray(candidates))people=candidates;
    }
    const positiveId=value=>Number.isInteger(Number(value))&&Number(value)>0?Number(value):null;
    return {jobId:positiveId(jobId),scoreId:positiveId(scoreId),people};
  }
  function addMessage(role,content,rows=[]){
    conversationMode(true);
    const article=document.createElement('article');article.className='assistant-message '+(role==='user'?'user':'assistant');
    const avatar=document.createElement('div');avatar.className='message-avatar';avatar.textContent=role==='user'?'你':'✧';avatar.setAttribute('aria-hidden','true');
    const body=document.createElement('div');body.className='message-body';const text=document.createElement('div');text.className='message-text';text.textContent=content||'本轮处理完成';body.append(text);
    if(rows.length){const details=document.createElement('details');details.className='message-tools';const summary=document.createElement('summary');summary.textContent='查看本轮执行记录';const list=document.createElement('ul');
      for(const row of rows){const li=document.createElement('li'),mark=document.createElement('span');mark.textContent=row.result?.pending?'◷':row.result?.success?'✓':'!';li.append(mark,document.createTextNode(toolLabels[row.tool]||row.description||'处理招聘需求'));list.append(li);}details.append(summary,list);body.append(details);
      const {jobId,scoreId,people}=resultContext(rows);if(people.length){const cards=document.createElement('div');cards.className='message-results';const heading=document.createElement('h3');heading.textContent='值得进一步沟通的人选';cards.append(heading);people.slice(0,5).forEach((person,index)=>{
        const card=document.createElement('div');card.className='assistant-person';card.style.setProperty('--order',index);const icon=document.createElement('span');icon.className='person-monogram';icon.textContent=(person.name||'人').slice(0,1);
        const info=document.createElement('div'),name=document.createElement('strong'),meta=document.createElement('p');name.textContent=person.name||'未命名人选';meta.textContent=[person.title,person.company,person.location].filter(Boolean).join(' · ');info.className='person-info';info.append(name,meta);const assessment=person.score100||person.analysis||{},excerpt=person._summary||assessment.summary;if(typeof excerpt==='string'&&excerpt){const reason=document.createElement('p');reason.className='person-reason';reason.textContent=excerpt;info.append(reason);}card.append(icon,info);
        if(typeof person._score==='number'&&Number.isFinite(person._score)){const score=document.createElement('div');score.className='person-score';score.append(document.createTextNode(String(person._score)));const small=document.createElement('small');small.textContent='匹配参考';score.append(small);card.append(score);}cards.append(card);
      });body.append(cards);}if(jobId){const actions=document.createElement('div');actions.className='message-actions';actions.append(link('/candidates?job='+jobId,'查看这个项目的人选 ↗'),link(scoreId?'/report?id='+scoreId:'/report','查看匹配报告 ↗'));body.append(actions);}
    }
    article.append(avatar,body);feed.append(article);feed.scrollTop=feed.scrollHeight;
  }
  function link(url,text){const a=document.createElement('a');a.href=url;a.textContent=text;return a;}
  function addThinking(){if(document.getElementById('assistant-thinking'))return;conversationMode(true);const el=document.createElement('article');el.className='assistant-message';el.id='assistant-thinking';el.innerHTML='<div class="message-avatar" aria-hidden="true">✧</div><div class="message-body"><div class="thinking-wave" aria-hidden="true"><i></i><i></i><i></i><i></i></div><div class="thinking-label"></div></div>';feed.append(el);updateThinking();feed.scrollTop=feed.scrollHeight;}
  function updateThinking(){const el=document.querySelector('.thinking-label');if(el)el.textContent=currentRun?.label||'正在理解你的招聘需求…';}
  async function renderHistory(force=false){const r=await api('/api/chat/history');if(!r.success)return false;const rows=r.data||[],serialized=JSON.stringify(rows);if(!force&&serialized===lastHistory)return true;lastHistory=serialized;feed.replaceChildren();conversationMode(rows.length>0);for(const row of rows){if(['user','assistant'].includes(row.role))addMessage(row.role,row.content,row.details||[]);}if(busy)addThinking();return true;}
  function renderMission(run){
    const previousRun=currentRun;currentRun=run;const badge=document.getElementById('mission-live'),goal=document.getElementById('mission-goal'),message=document.querySelector('#mission-message p');
    space.dataset.runStatus=run?.status||'idle';const title=document.getElementById('mission-title');title.textContent=!run?'从需求，到人选。':run.status==='running'?(labels[run.phase]||'推进招聘中'):run.status==='done'?'推荐已整理':'需要你的关注';
    badge.textContent=!run?'待开始':run.status==='running'?'处理中':run.status==='done'?'已回复':'需关注';goal.textContent=run?.message||'';goal.hidden=!goal.textContent;
    const phase=run?.phase==='planning'?'understanding':run?.phase,order=['understanding','searching','evaluating','recommending'],index=order.indexOf(phase);
    // Completed steps require observed execution, never a timer or guessed percentage.
    document.querySelectorAll('.mission-steps li').forEach((li,i)=>{
      const seen=run?.completed_phases||[],wasCurrent=li.classList.contains('current'),wasCompleted=li.classList.contains('completed');
      const isCurrent=run?.status==='running'&&i===index,isCompleted=seen.includes(li.dataset.phase)&&!isCurrent;
      li.classList.toggle('current',isCurrent);li.classList.toggle('completed',isCompleted);
      if(isCurrent)li.setAttribute('aria-current','step');else li.removeAttribute('aria-current');
      if(previousRun&&((isCurrent&&!wasCurrent)||(isCompleted&&!wasCompleted)))window.MinglieMotion?.pulse(li.querySelector('.mission-node'));
    });
    if(previousRun&&(previousRun.phase!==run?.phase||previousRun.status!==run?.status))window.MinglieMotion?.arrive(title);
    message.textContent=run?.label||'';document.getElementById('mission-message').hidden=!run;
    const counts=document.getElementById('mission-counts');const metrics=[];if(run?.scrape&&run.scrape.result_count!=null)metrics.push([run.scrape.result_count,'已收集']);if(run?.score&&run.score.total_count!=null)metrics.push([run.score.total_count,run.score.status==='done'?'已分析':'待分析']);
    const metricsKey=JSON.stringify(metrics);if(counts.dataset.metrics!==metricsKey){counts.dataset.metrics=metricsKey;counts.replaceChildren();
    for(const [value,label] of metrics){const div=document.createElement('div'),strong=document.createElement('strong'),small=document.createElement('small');strong.textContent=value;small.textContent=label+'份';div.append(strong,small);counts.append(div);}counts.hidden=!metrics.length;if(previousRun)window.MinglieMotion?.arrive(counts);}
    const links=document.getElementById('mission-links');links.replaceChildren();if(run?.job_id)links.append(link('/candidates?job='+Number(run.job_id),'查看人选 ↗'));if(run?.status==='attention'||run?.status==='error')links.append(run?.scrape_task_id?link('/scrape'+(run.job_id?'?job='+Number(run.job_id):''),'处理搜寻任务 ↗'):link('/logs','查看任务详情 ↗'));links.hidden=!links.children.length;
    if(run?.scrape?.status==='attention')message.textContent='平台需要你处理验证。搜寻结果会保留，可在任务详情中继续。';
    updateThinking();
  }
  async function refreshRecent(){const r=await api('/api/jobs');if(!r.success)return;const box=document.getElementById('recent-missions');box.replaceChildren();if(!r.data?.length){const card=document.createElement('div');card.className='recent-empty';const p=document.createElement('p'),strong=document.createElement('strong'),button=document.createElement('button');strong.textContent='还没有招聘项目';p.append(strong);button.type='button';button.textContent='开始第一轮招聘 ↗';button.addEventListener('click',()=>{input.focus();input.scrollIntoView({block:'center',behavior:matchMedia('(prefers-reduced-motion:reduce)').matches?'auto':'smooth'});});card.append(p,button);box.append(card);return;}
    for(const job of r.data.slice(0,3)){const a=document.createElement('a');a.className='recent-mission';a.href='/?job='+job.id;const strong=document.createElement('strong'),small=document.createElement('small');strong.textContent=job.name;small.textContent='让助手继续这个项目 ↗';a.append(strong,small);box.append(a);}
    const jobParam=new URLSearchParams(location.search).get('job');if(jobParam&&!input.value){const job=r.data.find(j=>String(j.id)===jobParam);if(job)usePrompt('继续招聘项目“'+job.name+'”（岗位ID：'+job.id+'），先查看当前进展，再告诉我下一步。');}
  }
  function configuredLabel(){const label=document.getElementById('connection-label');label.replaceChildren();if(modelConfigured){const detail=document.createElement('details'),summary=document.createElement('summary'),note=document.createElement('p');detail.className='quiet-help model-status';summary.textContent='模型已配置';note.textContent='已保存模型配置，连接情况以实际执行为准。';detail.append(summary,note);label.append(detail);}else label.append(link('/settings#model','接入模型 ↗'));}
  function schedulePoll(){clearTimeout(pollTimer);pollTimer=setTimeout(poll,document.hidden?5000:1800);}
  async function poll(){const r=await api('/api/chat/run');if(!r.success){document.getElementById('mission-live').textContent='连接中断';document.querySelector('#mission-message p').textContent='正在重新连接，任务不会重复提交。';schedulePoll();return;}
    if(!r.data){setBusy(false);currentRun=null;document.getElementById('assistant-thinking')?.remove();renderMission(null);addMessage('assistant','服务状态已重置，请先查看任务动态，确认原任务进展后再继续。');return;}
    renderMission(r.data);if(r.data.status==='running'){setBusy(true);addThinking();schedulePoll();}else{setBusy(false);document.getElementById('assistant-thinking')?.remove();await renderHistory(true);refreshRecent();}
  }
  async function submit(event){event?.preventDefault();const message=input.value.trim();if(!message||busy||!ready)return;setBusy(true);
    const r=await api('/api/chat/run',{method:'POST',body:JSON.stringify({message})});if(!r.success){if(r.data?.status==='running'){renderMission(r.data);await renderHistory(true);addThinking();schedulePoll();return;}setBusy(false);toast(r.error||'本次需求未提交','error');if(!modelConfigured)input.focus();return;}
    window.MinglieMotion?.pulse(document.getElementById('assistant-form'));
    input.value='';rememberDraft();addMessage('user',message);renderMission(r.data);addThinking();schedulePoll();
  }
  document.getElementById('assistant-form').addEventListener('submit',submit);input.addEventListener('input',rememberDraft);input.addEventListener('keydown',e=>{if(e.key==='Enter'&&!e.shiftKey&&!e.isComposing){e.preventDefault();submit();}});
  document.querySelectorAll('[data-prompt]').forEach(button=>button.addEventListener('click',()=>usePrompt(button.dataset.prompt)));
  // Dismiss contextual tips without collapsing the explanatory sections below.
  document.addEventListener('click',event=>document.querySelectorAll('.quiet-help[open]').forEach(tip=>{if(!tip.contains(event.target))tip.open=false;}));
  document.addEventListener('keydown',event=>{if(event.key!=='Escape')return;document.querySelectorAll('.quiet-help[open]').forEach(tip=>{if(tip.contains(document.activeElement))tip.querySelector('summary').focus();tip.open=false;});});
  clear.addEventListener('click',async()=>{if(busy||!ready)return;if(feed.children.length&&!confirm('开始新对话会清空对话记录，招聘项目和简历会保留。继续吗？'))return;clear.disabled=true;const r=await api('/api/chat/clear',{method:'POST'});if(!r.success){toast(r.error,'error');clear.disabled=false;return;}lastHistory='';feed.replaceChildren();conversationMode(false);renderMission(null);setBusy(false);input.focus();});
  input.value=readState(draftKey,'');const initial=new URLSearchParams(location.search).get('prompt');if(initial){if(!input.value.trim())input.value=initial;else if(input.value!==initial)toast('已有招聘草稿已保留，请先处理草稿，再填入示例需求');}rememberDraft();setBusy(false);
  (async()=>{const run=await api('/api/chat/run');if(!run.success){document.getElementById('connection-label').textContent='助手状态暂时不可用，请刷新后重试';return;}modelConfigured=run.model_configured;configuredLabel();busy=run.data?.status==='running';await renderHistory(true);renderMission(run.data);ready=true;setBusy(busy);if(busy){addThinking();schedulePoll();}refreshRecent();})();
})();
