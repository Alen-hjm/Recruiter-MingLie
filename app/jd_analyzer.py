"""
JD 智能拆解模块 - Agent 分析岗位描述，提取行业、职业、关键词
"""
import json
import httpx
from typing import Optional


# 行业方向预设
INDUSTRY_MAP = {
    "互联网": ["互联网", "IT", "软件", "SaaS", "电商平台", "游戏", "社交", "O2O"],
    "金融": ["银行", "证券", "基金", "保险", "金融科技", "FinTech", "支付"],
    "制造业": ["制造", "汽车", "机械", "电子", "半导体", "新能源"],
    "医疗健康": ["医疗", "医药", "健康", "生物科技", "医疗器械"],
    "教育": ["教育", "培训", "在线教育", "EdTech"],
    "房地产": ["房地产", "地产", "物业", "建筑"],
    "消费品": ["快消", "零售", "消费品", "奢侈品", "美妆"],
    "企业服务": ["企业服务", "SaaS", "云计算", "大数据", "AI"],
}

# 职业类型预设
JOB_TYPE_MAP = {
    "技术": ["开发", "工程师", "架构", "运维", "测试", "算法", "数据"],
    "产品": ["产品", "PM", "产品经理", "需求分析"],
    "设计": ["设计", "UI", "UX", "交互", "视觉"],
    "运营": ["运营", "增长", "用户", "内容", "社区", "活动"],
    "市场": ["市场", "品牌", "营销", "推广", "公关"],
    "销售": ["销售", "商务", "BD", "客户"],
    "人力": ["人力", "HR", "招聘", "培训", "HRBP"],
    "财务": ["财务", "会计", "审计", "税务", "出纳"],
    "法务": ["法务", "法务", "合规", "知识产权"],
}


async def analyze_jd_with_llm(
    job_name: str,
    job_description: str,
    api_key: str,
    base_url: str = "https://api.deepseek.com",
    model: str = "deepseek-chat",
) -> dict:
    """
    调用 LLM 分析 JD，返回结构化的岗位拆解结果
    """
    prompt = f"""你是一个专业的招聘顾问。请分析以下岗位信息，返回 JSON 格式的结构化结果。

## 岗位名称
{job_name}

## 岗位描述
{job_description}

## 要求
请返回以下 JSON 格式（不要返回其他内容，只返回 JSON）：

```json
{{
    "industry": "行业方向（如：互联网、金融、制造业等）",
    "job_type": "职业类型（如：技术、产品、设计、运营等）",
    "job_sub_type": "具体职位（如：前端开发、Java后端、产品经理等）",
    "hard_requirements": [
        "硬性要求1",
        "硬性要求2"
    ],
    "soft_requirements": [
        "软性要求1",
        "软性要求2"
    ],
    "plus_requirements": [
        "加分项1",
        "加分项2"
    ],
    "keywords": [
        "搜索关键词1",
        "搜索关键词2",
        "搜索关键词3"
    ],
    "scoring_focus": "评分重点说明（一句话概括应该重点看什么）",
    "risk_points": [
        "需要注意的风险点1",
        "需要注意的风险点2"
    ]
}}
```

注意：
1. keywords 用于在招聘平台搜索简历，应该是技能、工具、技术栈等
2. hard_requirements 是必须满足的条件
3. soft_requirements 是优先考虑的条件
4. plus_requirements 是加分项
5. risk_points 是筛选简历时需要注意的风险（如频繁跳槽、学历不符等）"""

    try:
        async with httpx.AsyncClient(timeout=60) as client:
            response = await client.post(
                f"{base_url}/chat/completions",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": model,
                    "messages": [
                        {"role": "system", "content": "你是一个专业的招聘顾问，擅长分析岗位描述并提取关键信息。请只返回 JSON 格式的结果。"},
                        {"role": "user", "content": prompt},
                    ],
                    "temperature": 0.3,
                    "max_tokens": 2000,
                },
            )
            response.raise_for_status()
            result = response.json()
            content = result["choices"][0]["message"]["content"]

            # 提取 JSON
            content = content.strip()
            if content.startswith("```"):
                content = content.split("```")[1]
                if content.startswith("json"):
                    content = content[4:]

            return json.loads(content)

    except Exception as e:
        print(f"[JD分析] LLM 调用失败: {e}")
        return None


def quick_classify(job_name: str, job_description: str) -> dict:
    """
    快速分类（不调用 LLM，基于关键词匹配）
    """
    text = f"{job_name} {job_description}".lower()

    # 识别行业
    industry = "其他"
    for ind, keywords in INDUSTRY_MAP.items():
        if any(kw.lower() in text for kw in keywords):
            industry = ind
            break

    # 识别职业类型
    job_type = "其他"
    for jt, keywords in JOB_TYPE_MAP.items():
        if any(kw.lower() in text for kw in keywords):
            job_type = jt
            break

    return {
        "industry": industry,
        "job_type": job_type,
    }


def generate_default_keywords(job_name: str, job_description: str) -> list[str]:
    """
    根据岗位名称和描述生成默认搜索关键词
    """
    keywords = []

    # 从岗位名称提取
    name_parts = job_name.replace("/", " ").replace("\\", " ").replace("-", " ").split()
    keywords.extend([p for p in name_parts if len(p) > 1])

    # 从描述中提取常见技术词
    tech_keywords = [
        "Python", "Java", "Go", "C++", "JavaScript", "TypeScript",
        "React", "Vue", "Angular", "Node.js", "Spring", "Django",
        "MySQL", "PostgreSQL", "MongoDB", "Redis", "Elasticsearch",
        "Docker", "Kubernetes", "AWS", "Azure", "Linux",
        "机器学习", "深度学习", "NLP", "计算机视觉", "大模型",
        "产品设计", "用户研究", "数据分析", "SQL", "Tableau",
    ]

    desc_lower = job_description.lower()
    for kw in tech_keywords:
        if kw.lower() in desc_lower:
            keywords.append(kw)

    return list(set(keywords))[:10]  # 去重，最多10个


async def analyze_jd(
    job_name: str,
    job_description: str,
    api_key: str = "",
    base_url: str = "https://api.deepseek.com",
    model: str = "deepseek-chat",
    use_llm: bool = True,
) -> dict:
    """
    分析 JD 的主入口
    - use_llm=True 且有 api_key：调用 LLM 深度分析
    - 否则：使用快速分类
    """
    if use_llm and api_key:
        result = await analyze_jd_with_llm(job_name, job_description, api_key, base_url, model)
        if result:
            return result

    # 降级到快速分类
    quick = quick_classify(job_name, job_description)
    keywords = generate_default_keywords(job_name, job_description)

    return {
        "industry": quick["industry"],
        "job_type": quick["job_type"],
        "job_sub_type": job_name,
        "hard_requirements": [],
        "soft_requirements": [],
        "plus_requirements": [],
        "keywords": keywords,
        "scoring_focus": "请根据岗位描述综合评估",
        "risk_points": [],
    }


def extract_filter_rules_from_jd(jd_analysis: dict, job_description: str = "") -> dict:
    """从 JD 分析结果 + 原始描述中提取简历卡片快速筛选规则
    
    返回的 dict 可直接传给 LiepinHunterScraper 的 quick_filter 参数。
    只使用三个筛选维度：学历、工作年限、年龄。
    """
    import re

    rules = {}
    hard_reqs = jd_analysis.get("hard_requirements", [])
    all_text = " ".join(hard_reqs) + " " + (job_description or "")

    # ── 学历要求 ──
    # 从硬需求和 JD 原文中提取学历要求
    edu_keywords = {
        "博士": ["博士", "PhD", "phd"],
        "硕士": ["硕士", "研究生", "Master"],
        "本科": ["本科", "学士", "Bachelor"],
        "大专": ["大专", "专科"],
    }
    required_edu = ""
    # 匹配模式："本科及以上"、"硕士及以上"、"本科学历" 等
    edu_patterns = [
        r"(博士|硕士|本科|大专|专科)(及)?以上",
        r"(博士|硕士|本科|大专|专科)(学历|学位)",
        r"学历[要求：:]*\s*(博士|硕士|本科|大专|专科)",
    ]
    for pattern in edu_patterns:
        m = re.search(pattern, all_text)
        if m:
            required_edu = m.group(1)
            break
    # 如果要求"本科及以上"，则跳过比本科低的学历
    if required_edu:
        edu_order = ["大专", "本科", "硕士", "博士"]
        if required_edu in edu_order:
            idx = edu_order.index(required_edu)
            # 跳过比要求低的学历
            skip = edu_order[:idx]  # 要求本科 → 跳过大专
            if skip:
                rules["skip_education"] = skip

    # ── 工作年限要求 ──
    exp_patterns = [
        r"(\d+)\s*年以上[工作经]",
        r"(\d+)\s*[年]\+(?:\s*经验)?",
        r"经验[要求：:]*\s*(\d+)\s*年",
        r"工作年限[要求：:]*\s*(\d+)\s*年",
        r"(\d+)\s*年[以上以].*(?:经验|经历|工作)",
        r"(?:经验|经历|工作)[^。，]*(\d+)\s*年",
    ]
    for pattern in exp_patterns:
        m = re.search(pattern, all_text)
        if m:
            rules["min_experience_years"] = int(m.group(1))
            break

    # ── 年龄要求 ──
    age_patterns = [
        r"(\d+)\s*[-~至到]\s*(\d+)\s*岁",
        r"年龄[要求：:]*\s*(\d+)\s*[-~至到]\s*(\d+)",
        r"(\d+)\s*岁以下",
        r"(\d+)\s*岁以上",
    ]
    for pattern in age_patterns:
        m = re.search(pattern, all_text)
        if m:
            groups = m.groups()
            if len(groups) == 2:
                rules["min_age"] = int(groups[0])
                rules["max_age"] = int(groups[1])
            elif "以下" in all_text[m.start():m.end()]:
                rules["max_age"] = int(groups[0])
            elif "以上" in all_text[m.start():m.end()]:
                rules["min_age"] = int(groups[0])
            break

    return rules
