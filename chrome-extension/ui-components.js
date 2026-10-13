/* Adapted from React Bits SpotlightCard (David Haz, 2025).
 * Source revision and MIT + Commons Clause notice: docs/ui-components.md and
 * chrome-extension/THIRD_PARTY_NOTICES.md. Native DOM port for Minglie only.
 */
(() => {
  const reduce = matchMedia('(prefers-reduced-motion: reduce)');
  const fine = matchMedia('(pointer: fine)');
  const spotSelector = '[data-ui-spotlight],.candidate,.assistant-person,.recent-mission,.overview-project';
  const selector = `${spotSelector},[data-ui-star-border]`;
  const mounted = new WeakSet();
  let frame = 0;
  const pending = new Map();

  const observer = new IntersectionObserver(entries => entries.forEach(entry => {
    entry.target.classList.toggle('ml-offscreen', !entry.isIntersecting);
  }));
  function decoration(className) {
    const el = document.createElement('span');
    el.className = className;
    el.setAttribute('aria-hidden', 'true');
    return el;
  }
  function mount(el) {
    if (mounted.has(el)) { observer.observe(el); return; }
    mounted.add(el);
    if (el.matches(spotSelector)) {
      el.classList.add('ml-spotlight');
      el.append(decoration('ml-spotlight-light'));
    }
    if (el.matches('[data-ui-star-border]')) {
      el.classList.add('ml-star-border');
      const edge = decoration('ml-star-edge');
      edge.append(decoration('ml-star-top'), decoration('ml-star-bottom'));
      el.append(edge);
    }
    observer.observe(el);
  }
  function scan(root) {
    if (root.matches?.(selector)) mount(root);
    root.querySelectorAll?.(selector).forEach(mount);
  }
  scan(document);
  new MutationObserver(records => records.forEach(record => {
    record.addedNodes.forEach(node => { if (node.nodeType === 1) scan(node); });
    record.removedNodes.forEach(node => {
      if (node.nodeType !== 1 || node.isConnected) return;
      [node, ...node.querySelectorAll(selector)].forEach(el => {
        observer.unobserve(el);
        pending.delete(el);
        // Keep the mount marker: a detached node may be reinserted with its decoration.
      });
    });
  })).observe(document.body, {childList: true, subtree: true});

  // Original SpotlightCard coordinate calculation, batched per frame. No idle loop.
  document.addEventListener('pointermove', event => {
    if (reduce.matches || !fine.matches || document.hidden) return;
    const el = event.target.closest?.('.ml-spotlight');
    if (!el) return;
    pending.set(el, {x: event.clientX, y: event.clientY});
    if (frame) return;
    frame = requestAnimationFrame(() => {
      frame = 0;
      pending.forEach(({x, y}, surface) => {
        if (!surface.isConnected) return;
        const rect = surface.getBoundingClientRect();
        surface.style.setProperty('--mouse-x', `${x - rect.left}px`);
        surface.style.setProperty('--mouse-y', `${y - rect.top}px`);
      });
      pending.clear();
    });
  }, {passive: true});
  document.addEventListener('focusin', event => {
    const el = event.target.closest?.('.ml-spotlight');
    if (!el) return;
    el.style.setProperty('--mouse-x', '50%');
    el.style.setProperty('--mouse-y', '50%');
  });
  function stop() {
    cancelAnimationFrame(frame);
    frame = 0;
    pending.clear();
  }
  function visibility() {
    document.documentElement.classList.toggle('ml-paused', document.hidden);
    if (document.hidden) stop();
  }
  document.addEventListener('visibilitychange', visibility);
  reduce.addEventListener('change', stop);
  visibility();
})();
