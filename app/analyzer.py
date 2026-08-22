"""
LLM 简历分析器
- 结构化解析简历内容
- 基于岗位要求匹配打分
- 支持知识库增强评分
"""

import json
import re
from rich.console import Console

try:
    from openai import OpenAI
except ImportError:
    OpenAI = None

console = Console()


class ResumeAnalyzer:
    """用LLM分析简历（支持知识库增强）"""

    def __init__(self, config: dict):
        llm_cfg = config.get("llm", {})
        self.model = llm_cfg.get("model", "gpt-4o-mini")
        self.job_description = config.get("job_description", "")
        self.knowledge_context = config.get("knowledge_context", "")

        if OpenAI is None:
            raise ImportError("openai 未安装，请运行: pip install openai")
        client_kwargs = {"api_key": llm_cfg.get("api_key", "")}
        if llm_cfg.get("base_url"):
            client_kwargs["base_url"] = llm_cfg["base_url"]
        self.client = OpenAI(**client_kwargs)

    def analyze_batch(self, resumes: list[dict]) -> list[dict]:
        """批量分析简历"""
        analyzed = []
        for i, resume in enumerate(resumes):
            console.print(f"[bold]分析 [{i+1}/{len(resumes)}] {resume.get('title', '?')}[/]")
            try:
                result = self.analyze_one(resume)
                resume["analysis"] = result
                score = result.get("total_score", 0)
                console.print(f"  → 评分: {score}/10  {result.get('recommendation', '')}")
            except Exception as e:
                console.print(f"  [red]分析失败: {e}[/]")
                resume["analysis"] = {"error": str(e)}
            analyzed.append(resume)
        return analyzed

    def analyze_one(self, resume: dict) -> dict:
        """分析单份简历（10分制，带 CoT 推理）"""
        resume_text = self._format_resume(resume)

        knowledge_block = ""
        if self.knowledge_context:
            knowledge_block = f"""
## 参考资料（来自知识库）
{self.knowledge_context}

"""

        prompt = f"""你是一个专业的招聘顾问。请分析以下简历与岗位的匹配度。

## 岗位要求
{self.job_description}

{knowledge_block}## 候选人简历
{resume_text}

## 输出要求

**第一步：在 <thinking> 标签中逐步推理**
<thinking>
1. 岗位核心要求是什么？（技术栈、经验、学历）
2. 候选人的技能匹配度如何？逐项对比。
3. 经验年限和行业匹配度？
4. 稳定性如何？每家公司待了多久？
5. 有哪些亮点和风险点？
6. 综合判断：推荐等级和分数。
</thinking>

**第二步：输出 JSON**
严格按以下JSON格式输出（不要输出其他内容）：
{{
  "skill_match": {{
    "score": 8,
    "detail": "熟悉Python和FastAPI，有3年经验..."
  }},
  "experience_match": {{
    "score": 7,
    "detail": "相关行业经验..."
  }},
  "education_match": {{
    "score": 9,
    "detail": "本科，计算机专业..."
  }},
  "stability": {{
    "score": 6,
    "detail": "平均每家公司2年..."
  }},
  "highlights": ["亮点1", "亮点2"],
  "concerns": ["风险点1"],
  "total_score": 7.5,
  "recommendation": "推荐面试/建议观望/不太合适",
  "summary": "一句话总结"
}}"""

        response = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": "你是专业招聘顾问，输出严格JSON格式。"},
                {"role": "user", "content": prompt},
            ],
            temperature=0.1,
        )

        content = response.choices[0].message.content

        # 提取 thinking 和 JSON
        thinking_text = ""
        json_content = content

        thinking_match = re.search(r'<thinking>(.*?)</thinking>', content, re.DOTALL)
        if thinking_match:
            thinking_text = thinking_match.group(1).strip()
            json_content = content[thinking_match.end():].strip()

        result = self._parse_json_robust(json_content)
        if result is None:
            result = {
                "total_score": 0,
                "recommendation": "评分失败",
                "summary": "LLM 输出格式异常",
                "_raw_content": content[:500],
            }

        if thinking_text:
            result['_thinking'] = thinking_text

        return result

    def score_resume_100(self, resume: dict, job_description: str) -> dict:
        """100分制评分，区分硬需求和软需求"""
        resume_text = self._format_resume(resume)

        knowledge_block = ""
        if self.knowledge_context:
            knowledge_block = f"""
## 参考资料（来自知识库）
{self.knowledge_context}

请特别关注参考资料中的：
- 评分要点和建议权重
- 简历风险点（anti_pitfalls）
- 硬需求模板

"""

        prompt = f"""你是一个专业的招聘顾问。请对以下候选人简历进行评分（100分制）。

## 评分规则
1. 先从岗位职责中提取【硬需求】和【软需求】
   - 硬需求：岗位明确要求的必备条件（如：必须X年经验、必须掌握某技术、必须某学历）
   - 软需求：加分项、优先条件（如：有XX经验优先、了解XX更好）

2. 评分维度（满分100）：
   - 学历匹配（10分）：是否达到岗位要求的学历
   - 经验年限（15分）：工作年限是否满足要求
   - 硬技能匹配（30分）：必须掌握的技术/技能掌握程度
   - 软技能匹配（15分）：优先技能、行业经验等
   - 项目经历（20分）：项目经验与岗位的相关性和深度
   - 综合素质（10分）：稳定性、成长性、沟通能力等

3. 硬需求判定：
   - 如果候选人不满足任何一条硬需求，总分最高59分（不及格）
   - 在"hard_requirements_met"中列出每条硬需求的满足情况

{knowledge_block}## 岗位职责
{job_description}

## 候选人简历
{resume_text}

## 输出要求（必须严格遵守）

**第一步：在 <thinking> 标签中逐步推理**
你必须先在 <thinking> 标签中完成以下分析，然后再输出 JSON。跳过推理直接给分是不允许的。

<thinking>
1. 硬需求提取：从岗位职责中逐条列出必须满足的条件（如：学历、年限、必须掌握的技术）
2. 硬需求逐条核验：
   - 需求1: [具体要求] → 候选人是否满足？引用简历中的具体证据。
   - 需求2: [具体要求] → 候选人是否满足？引用简历中的具体证据。
   - ...
3. 经验年限计算：候选人实际工作了几年？岗位要求几年？
4. 核心技能匹配：岗位要求的技术栈，候选人掌握哪些？不掌握哪些？匹配程度如何？
5. 项目经历评估：项目与岗位的相关性？深度如何？有无量化成果？
6. 风险点识别：频繁跳槽？职业断档？学历存疑？薪资预期过高？
7. 亮点提炼：哪些方面超出预期？
8. 综合判断：基于以上分析，给出各维度分数和总分。
</thinking>

**第二步：输出 JSON**
在推理完成后，严格按以下JSON格式输出（不要输出其他内容）：
{{
  "hard_requirements": ["硬需求1", "硬需求2"],
  "soft_requirements": ["软需求1", "软需求2"],
  "hard_requirements_met": [
    {{"requirement": "硬需求1", "met": true, "detail": "说明"}},
    {{"requirement": "硬需求2", "met": false, "detail": "说明"}}
  ],
  "scores": {{
    "education": {{"score": 8, "max": 10, "detail": "说明"}},
    "experience": {{"score": 12, "max": 15, "detail": "说明"}},
    "hard_skills": {{"score": 25, "max": 30, "detail": "说明"}},
    "soft_skills": {{"score": 10, "max": 15, "detail": "说明"}},
    "projects": {{"score": 15, "max": 20, "detail": "说明"}},
    "overall": {{"score": 8, "max": 10, "detail": "说明"}}
  }},
  "total_score": 78,
  "pass": true,
  "fail_reasons": [],
  "highlights": ["亮点1", "亮点2"],
  "concerns": ["风险点1"],
  "recommendation": "推荐面试/建议观望/不太合适",
  "summary": "一句话总结"
}}"""

        response = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": "你是专业招聘顾问。评分必须客观严格，硬需求不满足必须判不及格。\n\n" 
                                         "## 输出格式要求\n" 
                                         "你的回复必须包含两部分：\n" 
                                         "1. <thinking>...</thinking> 标签：在里面逐条分析硬需求核验、技能匹配、项目评估等。这是你的推理过程，必须在打分前完成。\n" 
                                         "2. JSON 对象：在 </thinking> 之后输出评分 JSON。\n\n" 
                                         "**禁止**跳过 <thinking> 直接输出 JSON。没有推理过程的评分会缺少关键证据，导致评分不准确。"},
                {"role": "user", "content": prompt},
            ],
            temperature=0.1,
        )

        content = response.choices[0].message.content

        # ── 提取 thinking 和 JSON ──
        thinking_text = ""
        json_content = content

        # 提取 <thinking> 标签中的推理过程
        thinking_match = re.search(r'<thinking>(.*?)</thinking>', content, re.DOTALL)
        if thinking_match:
            thinking_text = thinking_match.group(1).strip()
            # JSON 在 </thinking> 之后
            json_content = content[thinking_match.end():].strip()
        else:
            # 兼容：没有 thinking 标签，尝试从原始内容提取 JSON
            # 去掉可能的 markdown 代码块
            json_content = content.strip()
            if json_content.startswith('```'):
                # 提取代码块中的内容
                lines = json_content.split('\n')
                json_lines = []
                in_block = False
                for line in lines:
                    stripped = line.strip()
                    if stripped.startswith('```') and not in_block:
                        in_block = True
                        continue
                    elif stripped == '```' and in_block:
                        break
                    elif in_block:
                        json_lines.append(line)
                if json_lines:
                    json_content = '\n'.join(json_lines)

        # 解析 JSON
        result = self._parse_json_robust(json_content)
        if result is None:
            # 所有解析方式都失败，返回降级结果
            result = {
                "total_score": 0,
                "pass": False,
                "fail_reasons": ["LLM 输出格式异常，无法解析评分结果"],
                "highlights": [],
                "concerns": ["评分失败，请重试"],
                "recommendation": "评分失败",
                "summary": "LLM 返回内容无法解析",
                "_raw_content": content[:500],
            }

        # 附带推理过程（供调试和前端展示）
        if thinking_text:
            result['_thinking'] = thinking_text

        # 硬需求校验
        if result.get('hard_requirements_met'):
            all_met = all(r.get('met', False) for r in result['hard_requirements_met'])
            if not all_met and result.get('total_score', 0) >= 60:
                result['total_score'] = min(result['total_score'], 59)
                result['pass'] = False
                if 'fail_reasons' not in result:
                    result['fail_reasons'] = []
                result['fail_reasons'].append('硬需求未全部满足')

        return result

    @staticmethod
    def _parse_json_robust(text: str) -> dict | None:
        """多重容错解析 JSON"""
        text = text.strip()
        if not text:
            return None

        # 方法1: 直接解析
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass

        # 方法2: 提取 ```json ... ``` 代码块
        if '```' in text:
            lines = text.split('\n')
            json_lines = []
            in_json = False
            for line in lines:
                stripped = line.strip()
                if stripped.startswith('```') and not in_json:
                    in_json = True
                    continue
                elif stripped == '```' and in_json:
                    break
                elif in_json:
                    json_lines.append(line)
            if json_lines:
                try:
                    return json.loads('\n'.join(json_lines))
                except json.JSONDecodeError:
                    pass

        # 方法3: 找第一个 { 到最后一个 }
        start = text.find('{')
        end = text.rfind('}')
        if start != -1 and end != -1 and end > start:
            try:
                return json.loads(text[start:end + 1])
            except json.JSONDecodeError:
                pass

        return None

    def generate_report(self, analyzed_resumes: list[dict]) -> str:
        """生成汇总报告"""
        valid = [r for r in analyzed_resumes if "analysis" in r and "error" not in r.get("analysis", {})]
        valid.sort(key=lambda x: x["analysis"].get("total_score", 0), reverse=True)

        lines = [
            "# 简历筛选报告",
            f"",
            f"共分析 {len(analyzed_resumes)} 份简历，其中 {len(valid)} 份有效。",
            "",
            "## 排名",
            "",
        ]

        for i, r in enumerate(valid, 1):
            a = r["analysis"]
            lines.append(f"### {i}. {r.get('title', '?')} - {r.get('company', '?')}")
            lines.append(f"- **总分**: {a.get('total_score', '?')}/10")
            lines.append(f"- **推荐**: {a.get('recommendation', '?')}")
            lines.append(f"- **薪资**: {r.get('salary', '?')}")
            lines.append(f"- **亮点**: {', '.join(a.get('highlights', []))}")
            lines.append(f"- **风险**: {', '.join(a.get('concerns', []))}")
            lines.append(f"- **总结**: {a.get('summary', '')}")
            lines.append(f"- [详情链接]({r.get('detail_url', '#')})")
            lines.append("")

        recommendations = {}
        for r in valid:
            rec = r["analysis"].get("recommendation", "未知")
            recommendations[rec] = recommendations.get(rec, 0) + 1

        lines.append("## 统计")
        for rec, count in recommendations.items():
            lines.append(f"- {rec}: {count}人")

        return "\n".join(lines)

    def _format_resume(self, resume: dict) -> str:
        """把简历数据格式化为文本"""
        parts = []
        parts.append(f"姓名: {resume.get('name', '未知')}")
        parts.append(f"当前职位: {resume.get('current_position', resume.get('title', '未知'))}")
        parts.append(f"当前公司: {resume.get('current_company', resume.get('company', '未知'))}")
        parts.append(f"期望薪资: {resume.get('expected_salary', resume.get('salary', '未知'))}")
        parts.append(f"地点: {resume.get('location', '未知')}")
        parts.append(f"工作年限: {resume.get('experience', '未知')}")
        parts.append(f"学历: {resume.get('education', '未知')}")

        if resume.get("skill_tags"):
            parts.append(f"技能标签: {', '.join(resume['skill_tags'])}")
        if resume.get("work_experience"):
            parts.append(f"\n工作经历:\n{resume['work_experience']}")
        if resume.get("project_experience"):
            parts.append(f"\n项目经历:\n{resume['project_experience']}")
        if resume.get("education_experience"):
            parts.append(f"\n教育经历:\n{resume['education_experience']}")
        if resume.get("full_text"):
            parts.append(f"\n简历全文:\n{resume['full_text'][:3000]}")

        return "\n".join(parts)
