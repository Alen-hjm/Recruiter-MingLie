/* Aceternity-inspired interactions, adapted for the server-rendered workspace.
   No navigation interception, synthetic progress, or continuous JS render loop. */
(() => {
  const reduced = matchMedia('(prefers-reduced-motion: reduce)');
  const fine = matchMedia('(pointer: fine)');
  const cards = '.recent-mission,.overview-project,.project-card,.review-card,.conversation-card,.assistant-person';
  const surfaces = `${cards},[data-spotlight],.assistant-send`;
  const registered = new WeakSet(), active = new Map(), animations = new Set();
  const pending = new Map();
  let frame = 0;

  function play(el, frames, options = {}) {
    if (!el || reduced.matches || document.hidden || !el.getClientRects().length) return;
    const animation = el.animate(frames, {duration: 480, easing: 'cubic-bezier(.2,.8,.2,1)', ...options});
    animations.add(animation);
    const clean = () => animations.delete(animation);
    animation.onfinish = clean; animation.oncancel = clean;
  }
  function pulse(el) {
    play(el, [{boxShadow:'0 0 0 0 #8c8ee54d'}, {boxShadow:'0 0 0 12px #8c8ee500'}], {duration:650});
  }
  window.MinglieMotion = {pulse, arrive(el) {
    play(el, [{opacity:.35, translate:'0 8px'}, {opacity:1, translate:'0 0'}]);
  }};

  function reset(el) {
    pending.delete(el); active.delete(el); el.classList.remove('motion-hover');
    ['--tilt-x','--tilt-y','--drift-x','--drift-y','--magnet-x','--magnet-y'].forEach(key => el.style.removeProperty(key));
  }
  function flush() {
    frame = 0;
    pending.forEach(({x,y,bounds},el) => {
      if (!el.isConnected) return;
      const px = Math.max(0,Math.min(1,(x-bounds.left)/bounds.width));
      const py = Math.max(0,Math.min(1,(y-bounds.top)/bounds.height));
      el.style.setProperty('--spot-x', `${px*100}%`);
      el.style.setProperty('--spot-y', `${py*100}%`);
      el.style.setProperty('--tilt-x', `${(0.5-py)*4}deg`);
      el.style.setProperty('--tilt-y', `${(px-0.5)*4}deg`);
      el.style.setProperty('--drift-x', `${(px-0.5)*16}px`);
      el.style.setProperty('--drift-y', `${(py-0.5)*12}px`);
      el.style.setProperty('--magnet-x', `${(px-0.5)*7}px`);
      el.style.setProperty('--magnet-y', `${(py-0.5)*5}px`);
    });
    pending.clear();
  }
  const visibility = new IntersectionObserver(entries => entries.forEach(({target,isIntersecting}) => {
    target.classList.toggle('motion-offscreen', !isIntersecting);
    if (isIntersecting && target.dataset.motionReveal !== 'done') {
      target.dataset.motionReveal = 'done';
      play(target, [{opacity:.3,translate:'0 14px'}, {opacity:1,translate:'0 0'}], {duration:560});
    }
  }), {threshold:.05});
  function register(el) {
    if (registered.has(el)) return;
    registered.add(el);
    if (el.matches(cards)) el.classList.add('motion-card');
    visibility.observe(el);
    el.addEventListener('pointerenter', () => {
      if (reduced.matches || !fine.matches || el.disabled) return;
      active.set(el, el.getBoundingClientRect()); el.classList.add('motion-hover');
    });
    el.addEventListener('pointermove', event => {
      if (reduced.matches || !fine.matches || document.hidden || el.disabled) return;
      let bounds = active.get(el);
      if (!bounds) {bounds=el.getBoundingClientRect();active.set(el,bounds);el.classList.add('motion-hover');}
      pending.set(el,{x:event.clientX,y:event.clientY,bounds});
      if (!frame) frame=requestAnimationFrame(flush);
    });
    el.addEventListener('pointerleave', () => reset(el));
    el.addEventListener('pointercancel', () => reset(el));
  }
  function scan(root) {
    if (root.matches?.(surfaces)) register(root);
    root.querySelectorAll?.(surfaces).forEach(register);
  }
  scan(document);
  document.querySelectorAll('.mission-panel,.assistant-composer').forEach(el=>visibility.observe(el));
  new MutationObserver(records => records.forEach(record => {
    record.addedNodes.forEach(node => {if (node.nodeType===1) scan(node);});
    record.removedNodes.forEach(node => {
      if (node.nodeType!==1 || node.isConnected) return;
      [node,...node.querySelectorAll(surfaces)].forEach(el => {visibility.unobserve(el);reset(el);registered.delete(el);});
    });
  })).observe(document.getElementById('workspace'), {childList:true,subtree:true});

  // Native details retains keyboard behavior. Only the disclosed content animates.
  document.addEventListener('toggle', event => {
    if (event.target.tagName !== 'DETAILS' || !event.target.open) return;
    [...event.target.children].filter(el=>el.tagName!=='SUMMARY').forEach(el => {
      play(el,[{opacity:0,translate:'0 -6px'},{opacity:1,translate:'0 0'}],{duration:260});
    });
  },true);

  // One shared highlight follows the selected setup action, including keyboard focus.
  const grid = document.querySelector('.start-kit-grid');
  if (grid) {
    const highlight=document.createElement('span'); highlight.className='setup-hover';highlight.setAttribute('aria-hidden','true');grid.prepend(highlight);
    function follow(step) {
      const a=grid.getBoundingClientRect(), b=step.getBoundingClientRect();
      highlight.style.width=`${b.width+16}px`;highlight.style.height=`${b.height+16}px`;
      highlight.style.transform=`translate(${b.left-a.left-8}px,${b.top-a.top-8}px)`;
      grid.classList.add('has-hover');
    }
    grid.querySelectorAll('.start-kit-step').forEach(step=>{
      step.addEventListener('pointerenter',()=>follow(step));
      step.addEventListener('focusin',()=>follow(step));
      step.addEventListener('toggle',()=>follow(step),true);
    });
    grid.addEventListener('pointerleave',()=>{if (!grid.contains(document.activeElement)) grid.classList.remove('has-hover');});
    grid.addEventListener('focusout',event=>{if (!grid.contains(event.relatedTarget)) grid.classList.remove('has-hover');});
  }
  function stop() {
    cancelAnimationFrame(frame); frame=0;pending.clear();
    [...active.keys()].forEach(reset);
    animations.forEach(animation=>animation.cancel());
  }
  document.addEventListener('visibilitychange',()=>{
    document.documentElement.classList.toggle('motion-paused', document.hidden);
    if (document.hidden) stop();
  });
  document.documentElement.classList.toggle('motion-paused',document.hidden);
  reduced.addEventListener('change',()=>{if(reduced.matches)stop();});
  window.addEventListener('resize',()=>[...active.keys()].forEach(reset),{passive:true});
  window.addEventListener('scroll',()=>[...active.keys()].forEach(reset),{passive:true,capture:true});
})();
