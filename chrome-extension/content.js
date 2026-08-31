/**
 * 明猎 - 猎聘简历抓取 Content Script
 * 注入到 h.liepin.com 页面，读取简历卡片信息
 */

(() => {
  "use strict";

  // 防止重复注入
  if (window.__MINGLIE_INJECTED) return;
  window.__MINGLIE_INJECTED = true;

  console.log("[明猎] Content script 已加载");

  // ══════════════════════════════════════════
  // 简历卡片信息提取（复用原有逻辑）
  // ══════════════════════════════════════════

  function extractBasicInfo(card) {
    try {
      const getText = (el) => (el ? el.textContent.trim() : "");

      // 检测已阅标记
      let isViewed = false;
      for (const el of card.querySelectorAll("*")) {
        const text = getText(el);
        if (text === "阅" || text === "已阅") {
          isViewed = true;
          break;
        }
      }
      if (!isViewed) {
        const viewedEl = card.querySelector(
          '[class*="viewed"], [class*="read"], [class*="已读"], [class*="已看"]'
        );
        if (viewedEl) isViewed = true;
      }

      // 提取姓名
      let name = "";
      let activity = "";

      const emEl = card.querySelector("em");
      if (emEl) {
        const emText = getText(emEl);
        if (
          emText &&
          !emText.includes("活跃") &&
          !emText.includes("天内") &&
          !emText.includes("小时内")
        ) {
          name = emText;
        }
      }

      if (!name) {
        const nameEl =
          card.querySelector('[class*="name"]') ||
          card.querySelector('[class*="title"]');
        if (nameEl) {
          const nameText = getText(nameEl);
          if (
            nameText &&
            !nameText.includes("活跃") &&
            !nameText.includes("天内") &&
            !nameText.includes("小时内")
          ) {
            name = nameText;
          }
        }
      }

      // 活跃度
      for (const el of card.querySelectorAll(
        '[class*="active"], [class*="活跃"], span, div'
      )) {
        const text = getText(el);
        if (text && /\d+\s*(天|小时)内活跃/.test(text)) {
          activity = text;
          break;
        }
      }

      // 学历
      let education = "";
      for (const el of card.querySelectorAll(
        '[class*="edu"], [class*="学历"]'
      )) {
        const text = getText(el);
        if (
          text &&
          (text.includes("本科") ||
            text.includes("硕士") ||
            text.includes("博士") ||
            text.includes("大专") ||
            text.includes("MBA"))
        ) {
          education = text;
          break;
        }
      }

      // 工作年限
      let experience = "";
      for (const el of card.querySelectorAll(
        '[class*="exp"], [class*="经验"], [class*="year"]'
      )) {
        const text = getText(el);
        if (text && /\d+\s*年/.test(text)) {
          experience = text;
          break;
        }
      }

      // 所在地
      let location = "";
      for (const el of card.querySelectorAll(
        '[class*="area"], [class*="city"], [class*="location"], [class*="地点"], [class*="城市"]'
      )) {
        const text = getText(el);
        if (text && text.length < 20 && !text.includes("活跃")) {
          location = text;
          break;
        }
      }

      // 期望城市
      let expectCity = "";
      for (const el of card.querySelectorAll(
        '[class*="expect"], [class*="意向"], [class*="求职"]'
      )) {
        const text = getText(el);
        if (
          text &&
          /北京|上海|广州|深圳|杭州|成都|武汉|南京|西安|苏州|天津|重庆/.test(
            text
          )
        ) {
          expectCity = text;
          break;
        }
      }

      // 期望职位
      let expectPosition = "";
      for (const el of card.querySelectorAll("span, div, a")) {
        const text = getText(el);
        if (text && text.includes("求职职位")) {
          const m = text.match(/求职职位[：:]\s*(.+)/);
          if (m) expectPosition = m[1].trim();
          break;
        }
      }

      // 技能标签
      let skillTags = [];
      for (const el of card.querySelectorAll(
        '[class*="tag"], [class*="skill"], [class*="标签"], [class*="badge"]'
      )) {
        const text = getText(el);
        if (
          text &&
          text.length < 20 &&
          text.length > 1 &&
          !text.includes("活跃") &&
          !text.includes("立即")
        ) {
          skillTags.push(text);
        }
      }
      skillTags = [...new Set(skillTags)];

      // 公司和职位
      const companyEl = card.querySelector(
        '[class*="company"], [class*="公司"]'
      );
      const company = companyEl ? getText(companyEl) : "";

      const titleEl = card.querySelector(
        '[class*="position"], [class*="职位"], [class*="job-title"]'
      );
      const title = titleEl ? getText(titleEl) : "";

      // 详情页链接
      let detailUrl = "";
      const linkEl = card.querySelector('a[href*="res_id_encode"]');
      if (linkEl) {
        detailUrl = linkEl.href;
      } else {
        const anyLink = card.querySelector("a[href]");
        if (anyLink) detailUrl = anyLink.href;
      }

      if (!name && activity) name = activity;
      if (!name && !activity) name = "未知候选人";

      return {
        name,
        activity,
        education,
        experience,
        location,
        expect_city: expectCity,
        expect_position: expectPosition,
        skill_tags: skillTags,
        company,
        title,
        detail_url: detailUrl,
        is_viewed: isViewed,
      };
    } catch (e) {
      console.error("[明猎] 提取基础信息失败:", e);
      return null;
    }
  }

  // ══════════════════════════════════════════
  // 快速筛选（学历/年限/年龄）
  // ══════════════════════════════════════════

  function quickEvaluate(info, rules) {
    rules = rules || {};
    const education = info.education || "";
    const experience = info.experience || "";

    // 学历过滤
    const skipEdu = rules.skip_education || [];
    for (const edu of skipEdu) {
      if (education.includes(edu)) {
        return { pass: false, reason: `学历不达标: ${education}` };
      }
    }

    // 工作年限过滤
    const minExp = rules.min_experience_years || 0;
    if (minExp > 0 && experience) {
      const m = experience.match(/(\d+)/);
      if (m && parseInt(m[1]) < minExp) {
        return { pass: false, reason: `经验不足: ${experience}` };
      }
    }

    // 年龄过滤
    const minAge = rules.min_age || 0;
    const maxAge = rules.max_age || 0;
    if ((minAge > 0 || maxAge > 0) && info.age) {
      const ageMatch = info.age.match(/(\d+)/);
      if (ageMatch) {
        const age = parseInt(ageMatch[1]);
        if (minAge > 0 && age < minAge)
          return { pass: false, reason: `年龄过小: ${age}岁` };
        if (maxAge > 0 && age > maxAge)
          return { pass: false, reason: `年龄过大: ${age}岁` };
      }
    }

    return { pass: true, reason: "通过" };
  }

  // ══════════════════════════════════════════
  // 翻页
  // ══════════════════════════════════════════

  function findNextButton() {
    const selectors = [
      '[class*="next"]',
      '[class*="pagination"] .next',
      "li.ant-pagination-next",
      "button.ant-pagination-next",
      'a:has-text("下一页")',
    ];
    for (const sel of selectors) {
      const btn = document.querySelector(sel);
      if (btn && btn.offsetParent !== null) {
        const disabled = btn.getAttribute("disabled");
        const cls = btn.getAttribute("class") || "";
        if (!disabled && !cls.includes("disabled")) {
          return btn;
        }
      }
    }
    return null;
  }

  // ══════════════════════════════════════════
  // 抓取当前页所有简历卡片
  // ══════════════════════════════════════════

  function scrapeCurrentPage(filterRules) {
    const cards = document.querySelectorAll(
      '.tlog-common-resume-card, [class*="resume-card"]'
    );
    if (!cards.length) {
      return { resumes: [], total: 0, filtered: 0, skipped: 0 };
    }

    const resumes = [];
    let filtered = 0;
    let skippedViewed = 0;

    for (const card of cards) {
      if (!card.offsetParent) continue; // 不可见

      const info = extractBasicInfo(card);
      if (!info) continue;

      if (info.is_viewed) {
        skippedViewed++;
        continue;
      }

      // 快速筛选
      if (filterRules) {
        const evalResult = quickEvaluate(info, filterRules);
        if (!evalResult.pass) {
          filtered++;
          info._skip_reason = evalResult.reason;
          continue;
        }
      }

      resumes.push(info);
    }

    return {
      resumes,
      total: cards.length,
      filtered,
      skipped: skippedViewed,
    };
  }

  // ══════════════════════════════════════════
  // 详情页抓取（由侧边栏通过 background 协调）
  // ══════════════════════════════════════════

  // 从当前页面提取简历全文（如果已经在详情页）
  function extractFullText() {
    const body = document.body;
    if (!body) return '';
    const clone = body.cloneNode(true);
    clone.querySelectorAll('script,style,noscript,iframe,nav,header,footer').forEach(el => el.remove());
    return (clone.innerText || clone.textContent || '').trim().substring(0, 8000);
  }

  // ══════════════════════════════════════════
  // 简历详情页精准提取
  // ══════════════════════════════════════════

  function extractResumeDetail() {
    const url = window.location.href;
    const isLiepin = window.location.hostname.includes("liepin");

    if (!isLiepin) {
      return { success: false, error: "当前页面不是猎聘", detail_url: url };
    }

    // 尝试 CSS 选择器精准提取
    const selectors = [
      ".resume-content",
      ".resume-main",
      ".detail-content",
      ".resume-detail",
      "[class*='resume-box']",
      "[class*='resume-body']",
      "[class*='resume-info']",
      ".content-left",
      ".main-content",
    ];

    let resumeEl = null;
    for (const sel of selectors) {
      resumeEl = document.querySelector(sel);
      if (resumeEl && resumeEl.innerText.length > 200) break;
      resumeEl = null;
    }

    let fullText = "";
    let method = "";

    if (resumeEl) {
      // 精准提取：只取简历正文区域
      fullText = resumeEl.innerText.trim();
      method = "selector";
    } else {
      // 兜底：全页提取，过滤噪音
      const clone = document.body.cloneNode(true);
      clone.querySelectorAll(
        "script, style, noscript, iframe, nav, header, footer, " +
        "[class*='nav'], [class*='header'], [class*='footer'], " +
        "[class*='sidebar'], [class*='recommend'], [class*='ad-'], " +
        "[class*='banner'], [class*='modal'], [class*='popup']"
      ).forEach(el => el.remove());
      fullText = (clone.innerText || "").trim();
      method = "fallback";
    }

    // 截断保护
    if (fullText.length > 12000) {
      fullText = fullText.substring(0, 12000);
    }

    if (fullText.length < 50) {
      return { success: false, error: "页面内容过短，可能不是简历详情页", detail_url: url };
    }

    // 提取姓名（简单尝试）
    let name = "";
    const nameEl = document.querySelector(
      "[class*='name'], [class*='title'], h1, h2, .resume-name, .user-name"
    );
    if (nameEl) {
      const nameText = nameEl.innerText.trim();
      if (nameText.length > 1 && nameText.length < 20) {
        name = nameText;
      }
    }

    return {
      success: true,
      full_text: fullText,
      detail_url: url,
      name: name,
      method: method,
      text_length: fullText.length,
    };
  }

  // ══════════════════════════════════════════
  // 消息监听（与 popup/background 通信）
  // ══════════════════════════════════════════

  chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
    if (msg.action === "scrape_page") {
      const result = scrapeCurrentPage(msg.filterRules);
      sendResponse(result);
      return true;
    }

    if (msg.action === "get_resume_links") {
      // 获取当前页所有简历详情页链接
      const cards = document.querySelectorAll(
        '.tlog-common-resume-card, [class*="resume-card"]'
      );
      const links = [];
      for (const card of cards) {
        if (!card.offsetParent) continue;
        const link = card.querySelector('a[href*="res_id_encode"]') || card.querySelector('a[href]');
        if (link && link.href) {
          links.push({
            url: link.href,
            name: (card.querySelector('em') || {}).textContent || '',
          });
        }
      }
      sendResponse({ links });
      return true;
    }

    if (msg.action === "scrape_detail") {
      // 已经在详情页了，提取全文
      const text = extractFullText();
      sendResponse({ full_text: text, detail_url: window.location.href });
      return true;
    }

    if (msg.action === "scrape_resume") {
      // 精准提取简历详情页内容（CSS 选择器优先，兜底全页文本）
      const result = extractResumeDetail();
      sendResponse(result);
      return true;
    }

    if (msg.action === "get_full_text") {
      sendResponse({ text: extractFullText() });
      return true;
    }

    if (msg.action === "click_next_page") {
      const btn = findNextButton();
      if (btn) {
        btn.click();
        sendResponse({ success: true });
      } else {
        sendResponse({ success: false, error: "没有下一页" });
      }
      return true;
    }

    if (msg.action === "get_page_info") {
      const cards = document.querySelectorAll(
        '.tlog-common-resume-card, [class*="resume-card"]'
      );
      sendResponse({
        url: window.location.href,
        cardCount: cards.length,
        isLiepin: window.location.hostname.includes("liepin"),
        isDetailPage: window.location.href.includes("res_id_encode"),
      });
      return true;
    }

    if (msg.action === "ping") {
      sendResponse({ alive: true });
      return true;
    }
  });

  console.log("[明猎] Content script 就绪");
})();
