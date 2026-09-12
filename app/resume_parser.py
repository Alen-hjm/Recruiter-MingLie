# -*- coding: utf-8 -*-
"""
简历解析模块：把一大段简历全文切分成结构化段落。

设计原则（与整个项目一致）：
  1. 锚点切分 —— 中文简历的小标题是有强规律的（工作经历/项目经历/教育经历...），
     用这些词做锚点，两个锚点之间即该 section 的正文。
  2. 严格模式 —— 锚点没命中就留空，绝不用全文兜底填充。
     现状恰恰相反（插件侧失败就 fallback 全页 innerText，导致喂给 LLM 的全是噪音）。
  3. 可归因 —— 每个字段都带 confidence，让调用方知道哪些可信、哪些需要人工补。
"""

import re

# ---------------------------------------------------------------- 锚点词表
# 顺序即简历的常见顺序；第一个命中的作为该 section 的开始
ANCHORS = [
    ("job_intent", ["求职意向", "求职期望", "期望工作", "意向职位"]),
    ("work",       ["工作经历", "工作经验", "工作履历", "任职经历", "工作经验"]),
    ("project",    ["项目经历", "项目经验", "项目描述"]),
    ("education",  ["教育经历", "教育背景", "学历背景"]),
    ("skills",     ["专业技能", "技能特长", "技能标签", "专业能力", "职业技能"]),
    ("evaluation", ["自我评价", "个人评价", "自我描述", "个人总结"]),
    ("cert",       ["证书", "资格证书", "培训经历", "所获证书"]),
    ("award",      ["获奖经历", "荣誉奖励", "所获荣誉"]),
]

# 这些词出现在正文中会造成误判，需要更严格的匹配（见 _is_anchor_line）
_STRICT_MIN_LEN = 2
_STRICT_MAX_LEN = 8


def _is_anchor_line(line, words):
    """
    判断一行是否为锚点行。

    严格判定（避免把正文里的"工作经历丰富"当成标题）：
      - 去掉空白和标点后，整行严格等于某个锚点词；或
      - 整行长度 ≤ 8 且以锚点词开头（后面只允许冒号/数字，如"项目经历（共2段）"）
    """
    stripped = re.sub(r"[\s:：、,，.。\-—_（）()\[\]【】]", "", line).strip()
    if not stripped:
        return False
    for w in words:
        if stripped == w:
            return True
        # 允许 "项目经历（共2段）" 这种：以锚点开头，尾部只有少量字符
        if stripped.startswith(w) and len(stripped) - len(w) <= 4:
            return True
    return False


# 锚点词后面跟这些字 = 误判（正文里的"工作经历丰富"不是标题）
_FALSE_POSITIVE_AFTER = ("的", "了", "很", "较", "丰富", "非常", "比较", "十分", "较为")


def _find_anchor_positions(text):
    """
    在整段文本里定位锚点词的字符偏移。

    为什么不用"按行判断"：猎聘的 full_text 是从 innerText 压平提取的，
    整篇简历可能只有一行（实测 3515 字全在一行），按行判断会全部漏掉。
    所以改为在字符流里搜索锚点词，取每个 key 最早一次合理出现。
    """
    hits = []
    for key, words in ANCHORS:
        best = None
        for w in words:
            idx = 0
            while True:
                pos = text.find(w, idx)
                if pos < 0:
                    break
                after = text[pos + len(w): pos + len(w) + 2]
                if any(after.startswith(bad) for bad in _FALSE_POSITIVE_AFTER):
                    idx = pos + 1      # 是正文里的用法，继续往后找
                    continue
                if best is None or pos < best[0]:
                    best = (pos, w)
                break
        if best:
            hits.append((best[0], key, best[1]))
    hits.sort(key=lambda x: x[0])
    return hits


# 猎聘页面底部/侧边的噪音文案（不是简历内容），切到就截断
_PAGE_NOISE = [
    "本次搜索匹配到的简历", "简历不匹配声明", "该人选信息仅供公司招聘使用",
    "点击这里进行反馈", "立即沟通", "继续沟通", "超级聊聊", "查看联系方式",
    "收藏转发", "推荐职位", "金领券", "个人作品｜", "向TA索要", "已售出",
    "简历编号", "最后一次登录时间", "方便联系时间",
]


def _strip_page_noise(text, max_len=2000):
    """截断到第一条页面噪音之前，并限制长度（防自我评价把整个页面吃进来）"""
    if not text:
        return ""
    cut = len(text)
    for noise in _PAGE_NOISE:
        pos = text.find(noise)
        if 0 <= pos < cut:
            cut = pos
    out = text[:cut].strip()
    if len(out) > max_len:
        out = out[:max_len] + "…（已截断，原文 %d 字）" % len(text)
    return out


def split_sections(full_text, strict=True):
    """
    把简历全文按锚点切分成段落。

    返回:
      {
        "sections": {"work": {"content": "...", "confidence": "high"}, ...},
        "summary":  "锚点之前的开头摘要段（含性别/年龄/学历等）",
        "missing":  ["skills", "cert"],          # 未命中的 section
        "meta":     {"text_length": 3515, "anchors_hit": 5, "parser_version": "anchor-v1"}
      }
    """
    result = {
        "sections": {},
        "summary": "",
        "missing": [],
        "meta": {
            "text_length": len(full_text or ""),
            "anchors_hit": 0,
            "parser_version": "anchor-v1",
            "strict_mode": strict,
        },
    }
    if not full_text:
        result["missing"] = [k for k, _ in ANCHORS]
        return result

    text = full_text.replace("\r\n", "\n").replace("\r", "\n")

    # 在字符流里定位锚点（兼容"整篇压平一行"和"正常多行"两种情况）
    hits = _find_anchor_positions(text)

    if not hits:
        # 一个锚点都没命中：全文作为摘要，其余留空（严格模式不兜底）
        result["summary"] = text[:2000]
        result["missing"] = [k for k, _ in ANCHORS]
        result["meta"]["anchors_hit"] = 0
        return result

    # 第一个锚点之前的内容 = 开头摘要（含性别/年龄/学历/年限等）
    first_off = hits[0][0]
    result["summary"] = text[:first_off].strip()[:1000]

    # 相邻锚点之间 = 该 section 正文
    for idx, (off, key, word) in enumerate(hits):
        end = hits[idx + 1][0] if idx + 1 < len(hits) else len(text)
        content = text[off:end].strip()
        # 去掉锚点词本身（含"项目经历（共2段）"这类尾巴）
        if content.startswith(word):
            content = content[len(word):].lstrip("：: （(）)【】[]0123456789共段项").strip()
        content = _strip_page_noise(content, max_len=2500 if key in ("work", "project") else 1200)
        if not content:
            result["sections"][key] = {"content": "", "confidence": "low"}
            result["missing"].append(key)
            continue
        conf = "high" if len(content) > 20 else "medium"
        result["sections"][key] = {"content": content, "confidence": conf}

    result["missing"] = [k for k, _ in ANCHORS if k not in result["sections"]]
    result["meta"]["anchors_hit"] = len(hits)
    return result


# ---------------------------------------------------------------- 基本信息提取
_RE_GENDER_AGE = re.compile(r"(男|女)\s*(\d{1,2})\s*岁")
_RE_YEARS      = re.compile(r"(?:工作|经验|从业)\s*(\d{1,2})\s*年")
_RE_EDU        = re.compile(r"(博士|硕士|研究生|本科|大专|中专|高中|MBA|EMBA)")
_RE_NAME       = re.compile(r"([一-龥])\s*(先生|女士|小姐)")
_RE_SALARY     = re.compile(r"(\d{1,3})\s*[-~到]\s*(\d{1,3})\s*k", re.I)
_RE_CITY       = re.compile(r"(北京|上海|广州|深圳|杭州|成都|南京|武汉|西安|苏州|东莞|佛山|厦门|长沙|重庆|天津|郑州|青岛|合肥|宁波)")


def extract_basic(summary_text, full_text=""):
    """
    从开头摘要段提取基本信息。只提取"看得见的"，猜不到的一律留空。
    """
    src = (summary_text or "") + " " + (full_text or "")[:400]
    basic = {"gender": "", "age": "", "experience_years": "", "education": "", "city": ""}

    m = _RE_GENDER_AGE.search(src)
    if m:
        basic["gender"] = m.group(1)
        basic["age"] = m.group(2) + "岁"

    m = _RE_YEARS.search(src)
    if m:
        basic["experience_years"] = m.group(1) + "年"

    m = _RE_EDU.search(src)
    if m:
        basic["education"] = m.group(1)

    m = _RE_CITY.search(src)
    if m:
        basic["city"] = m.group(1)

    return basic


def extract_name(summary_text, full_text="", fallback_name=""):
    """
    提取候选人姓名。猎聘页面是脱敏的（"叶先生"），所以优先取"X先生/女士"，
    取不到才用传入的 name 字段，并标记 confidence。
    """
    src = (summary_text or "")[:200] + " " + (full_text or "")[:200]
    m = _RE_NAME.search(src)
    if m:
        return {"value": m.group(0), "confidence": "medium",
                "note": "页面脱敏显示（姓氏+称谓），真实姓名需人工确认"}
    if fallback_name and fallback_name.strip():
        return {"value": fallback_name.strip(), "confidence": "low",
                "note": "来自 name 字段，可能为登录用户名，需人工核对"}
    return {"value": "", "confidence": "low", "note": "未识别到姓名"}


def parse_resume(resume_dict):
    """
    统一入口：传入一条简历 dict（含 full_text），返回解析后的结构。

    返回结构同时供「推荐报告」与「后续的功能 C」使用。
    """
    full_text = (resume_dict or {}).get("full_text", "") or ""
    parsed = split_sections(full_text)
    basic = extract_basic(parsed["summary"], full_text)
    name_info = extract_name(parsed["summary"], full_text, (resume_dict or {}).get("name", ""))

    # 求职意向里的期望职位/薪资（若切出来了）
    expect = {"position": "", "salary": "", "city": basic.get("city", "")}
    ji = parsed["sections"].get("job_intent", {}).get("content", "")
    if ji:
        m = _RE_SALARY.search(ji)
        if m:
            expect["salary"] = f"{m.group(1)}-{m.group(2)}k"
        # 期望职位 = 开头到第一个数字/薪资为止（如 "CFO/财务VP50-65k×12薪广州" → "CFO/财务VP"）
        m = re.match(r"^([一-龥A-Za-z/·\s]+?)(?=\d|薪|k|K|$)", ji.strip())
        if m:
            pos = m.group(1).strip(" 、,，")
            if pos and len(pos) <= 30:
                expect["position"] = pos

    return {
        "name": name_info,
        "basic": basic,
        "expect": expect,
        "sections": parsed["sections"],
        "summary": parsed["summary"],
        "missing": parsed["missing"],
        "meta": parsed["meta"],
    }
