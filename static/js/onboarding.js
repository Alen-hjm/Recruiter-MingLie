/* Account-scoped, server-persisted guide. Examples never start recruiting. */
(() => {
  const dialog = document.getElementById('onboarding-dialog');
  if (!dialog) return;
  const example = '帮我找深圳的半导体销售总监，先收集10份简历，无其他要求。';
  const steps = [
    {title:'一句话，开始你的招聘',description:'告诉明猎你想招谁，助手会把需求变成可以执行的招聘任务。',points:['理解岗位和城市，建立招聘项目。','调用自动搜寻，保存简历并分析匹配。','在对话里交付人选，继续查看报告、跟进面试。'],note:'推荐是沟通的起点。经历与意向，仍需你和人选进一步核实。',action:'看看输入框',target:'composer'},
    {title:'给助手接入一个模型',description:'模型负责理解你的需求、规划任务和分析简历。选择服务商，填写模型名称与服务密钥，再保存。',points:['支持多种服务商，也可以填写自定义兼容服务。','密钥只需在设置里填写，不用粘贴到招聘对话。','岗位与简历内容将发送至你配置的模型服务。'],note:'读取配置中…',action:'去配置模型 ↗',target:'/settings#model'},
    {title:'搜寻时，登录你的猎聘账号',description:'助手会调用已有的自动搜寻功能。首次执行时，请在采集浏览器中完成登录或扫码。',points:['在下方输入需求，由助手开始搜寻，无需手动建项目。','遇到登录或平台验证提示，按任务提示处理；已有简历会保留。','处理后到“搜寻详情”查看任务，并按提示继续。'],note:'浏览器扩展是可选的辅助入口，不是自动搜寻的必需条件；当前无法从这里确认平台登录状态。',action:'查看搜寻详情 ↗',target:'/scrape'},
    {title:'现在，试着说出你的需求',description:'写清岗位、城市和想收集的份数，或直接粘贴 JD。只有真正缺少必要信息时，助手才会追问。',points:['你可以说：“先找10份”“无其他要求”。','发送后在右侧查看实际阶段和收集数量。','切换页面后，回到助手可以继续查看本轮进展。'],note:'点击“填入示例”只会填入输入框，不会提交或启动采集。',action:'填入示例，开始招聘 ↗',target:'example'}
  ];
  let state=null,index=0,saving=false,origin=null;
  const el=id=>document.getElementById(id);
  function paint() {
    const step=steps[index];
    el('onboarding-counter').textContent=`开始指南 / 0${index+1}`;
    el('onboarding-title').textContent=step.title;
    el('onboarding-description').textContent=step.description;
    const list=el('onboarding-points');list.replaceChildren();
    step.points.forEach(text=>{const li=document.createElement('li');li.textContent=text;list.append(li);});
    el('onboarding-note').textContent=index===1?state?.model_configured?'模型已配置。保存状态不代表已验证连通性，可以在发送需求前核对设置。':'尚未配置模型，接入后才可实际执行招聘需求。':step.note;
    el('onboarding-action').textContent=step.action;
    el('onboarding-next').textContent=index===3?'我知道了 ✓':'下一步 ↗';
    el('onboarding-back').hidden=index===0;
    const sample=el('onboarding-example');sample.hidden=![0,3].includes(index);sample.textContent=example;
    document.querySelectorAll('#onboarding-steps li').forEach((li,i)=>{li.classList.toggle('current',i===index);li.classList.toggle('past',i<index);if(i===index)li.setAttribute('aria-current','step');else li.removeAttribute('aria-current');});
  }
  async function persist(status,step=index) {
    if(saving)return false;
    saving=true;dialog.querySelectorAll('button').forEach(b=>b.disabled=true);
    try{const r=await api('/api/onboarding',{method:'POST',body:JSON.stringify({status,step})});if(!r.success){toast(r.error||'引导进度未保存，请重试','error');return false;}state=r.data;return true;}
    finally{saving=false;dialog.querySelectorAll('button').forEach(b=>b.disabled=false);}
  }
  function close(){dialog.close();origin?.focus();}
  async function dismiss(){if(await persist('skipped'))close();}
  async function open(){if(dialog.open||saving)return;origin=document.activeElement;const r=await api('/api/onboarding');if(!r.success){toast('暂时无法读取开始指南，请重试','error');return;}state=r.data;index=state.status==='completed'?0:state.step;paint();dialog.showModal();el('onboarding-next').focus();}
  document.querySelectorAll('[data-open-onboarding]').forEach(b=>b.addEventListener('click',open));
  el('onboarding-next').addEventListener('click',async()=>{if(index===3){if(await persist('completed'))close();}else if(await persist('in_progress',index+1)){index++;paint();}});
  el('onboarding-back').addEventListener('click',async()=>{if(await persist('in_progress',index-1)){index--;paint();}});
  el('onboarding-close').addEventListener('click',dismiss);el('onboarding-skip').addEventListener('click',dismiss);
  dialog.addEventListener('cancel',e=>{e.preventDefault();dismiss();});
  el('onboarding-action').addEventListener('click',async()=>{
    const target=steps[index].target;
    if(!await persist(target==='example'?'completed':'in_progress'))return;
    close();
    if(target==='example'||target==='composer'){
      const input=el('chat-input');
      if(!input){location.href=target==='example'?'/?prompt='+encodeURIComponent(example):'/';return;}
      if(target==='example'){
        if(input.value.trim()&&input.value!==example){toast('已有草稿已保留，请先处理草稿，再点击首页的示例需求');}
        else{input.value=example;input.dispatchEvent(new Event('input',{bubbles:true}));}
      }
      input.scrollIntoView({block:'center',behavior:matchMedia('(prefers-reduced-motion:reduce)').matches?'auto':'smooth'});input.focus();
    }else location.href=target;
  });
  api('/api/onboarding').then(r=>{if(!r.success)return;state=r.data;document.querySelectorAll('[data-model-readiness]').forEach(n=>{n.textContent=state.model_configured?'已配置':'待接入';n.classList.toggle('ready',state.model_configured);});if(state.status==='new')open();});
})();
