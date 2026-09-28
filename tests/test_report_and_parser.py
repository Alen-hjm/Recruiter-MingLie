# -*- coding: utf-8 -*-
"""简历解析与推荐报告引擎测试。

这两个模块是「零编造」设计的落点：报告里每个字段都必须能追溯到原文，
取不到就留空标记，绝不用全文兜底。所以测试重点是「不该出来的东西别出来」。
"""

from app.report_engine import build_report, render_markdown, safe_resolve
from app.resume_parser import parse_resume, split_sections

# 实测中猎聘 innerText 会把整份简历压成一行，这里刻意还原这种形态
FLATTENED_RESUME = (
    "叶先生 男 32岁 本科 8年经验 上海 "
    "求职意向 期望职位：后端开发工程师 期望城市：上海 期望薪资：30-40万 "
    "工作经历 2020.03-至今 某某科技有限公司 后端开发工程师 "
    "负责订单系统的架构设计与性能优化，QPS 从 800 提升到 5000。 "
    "项目经历 订单中台重构 主导拆分单体应用，引入消息队列削峰。 "
    "教育经历 2012.09-2016.06 某某大学 计算机科学与技术 本科 "
    "专业技能 精通 Python、Go，熟悉 MySQL、Redis、Kafka "
    "自我评价 八年后端经验，擅长高并发系统设计。"
)


def test_split_sections_on_flattened_text():
    """整篇压平一行时仍应命中锚点（原实现的坑）"""
    result = split_sections(FLATTENED_RESUME)
    sections = result["sections"]

    assert "work" in sections, "工作经历锚点未命中"
    assert "项目经历" not in sections["work"]["content"]
    assert "订单系统的架构设计" in sections["work"]["content"]
    assert "education" in sections
    assert "某某大学" in sections["education"]["content"]
    assert "skills" in sections
    assert "Python" in sections["skills"]["content"]
    assert result["meta"]["anchors_hit"] >= 4


def test_parser_does_not_fall_back_to_full_text():
    """严格模式：一个锚点都没命中时，不许拿全文填充 section"""
    result = split_sections("这是一段没有任何小标题的自由文本，讲了一些无关紧要的内容。")

    assert result["sections"] == {}
    assert result["missing"], "应当报告全部缺失"
    assert result["summary"], "开头摘要仍应保留"


def test_parser_avoids_false_positive_anchor():
    """「工作经历丰富」是正文而不是标题，不应被当成锚点切分"""
    text = "个人简介 工作经历丰富的候选人。自我评价 踏实肯干。"
    result = split_sections(text)
    # 「工作经历丰富」被排除后，work 不应被切出来
    assert "work" not in result["sections"]


def test_parse_resume_structure():
    parsed = parse_resume({"full_text": FLATTENED_RESUME, "name": "叶先生"})

    assert parsed["name"]["value"] == "叶先生"
    assert "sections" in parsed
    assert parsed["sections"]["work"]["confidence"] in ("high", "medium", "low")


def test_safe_resolve_only_supports_paths():
    """expr 只支持 a.b.c 取值，不做 eval —— 这是有意的安全选择"""
    ctx = {"a": {"b": {"c": 42}}, "list": [1, 2, 3]}

    assert safe_resolve("a.b.c", ctx) == 42
    assert safe_resolve("a.missing.c", ctx) == ""
    assert safe_resolve("", ctx) == ""
    # 表达式/下标一律不支持，返回空而不是执行
    assert safe_resolve("a['b']['c']", ctx) == ""
    assert safe_resolve("__import__('os').system('echo x')", ctx) == ""


def test_build_report_marks_missing_required():
    """必填字段缺失时必须显式标注，而不是留空糊过去"""
    resume = {"full_text": "只有一段没有锚点的文本", "name": ""}
    report = build_report(resume, score100={}, job={"name": "后端"})

    validation = report["validation"]
    assert validation["ok"] is False or validation["need_human"]
    assert any(
        f["field"] == "name" for f in validation.get("missing_required", [])
    )


def test_build_report_renders_markdown():
    resume = {
        "full_text": FLATTENED_RESUME,
        "name": "叶先生",
        "detail_url": "https://h.liepin.com/resume/show?res_id_encode=abc123",
    }
    score = {
        "total_score": 86,
        "highlights": ["高并发经验扎实", "技术栈匹配"],
        "concerns": ["期望薪资略高于预算"],
        "summary": "整体匹配度较高，建议安排初面。",
    }
    report = build_report(resume, score, job={"name": "后端开发工程师"})
    md = render_markdown(report)

    assert "叶先生" in md
    assert "86" in md
    assert "高并发经验扎实" in md
    assert "{{" not in md, "模板占位符残留"


def test_report_does_not_invent_content():
    """零编造：没有评分时不应凭空生成推荐理由"""
    resume = {"full_text": FLATTENED_RESUME, "name": "叶先生"}
    report = build_report(resume, score100=None, job={"name": "后端"})

    for section in report["sections"]:
        if section["key"] in ("recommend_reason", "overall_comment"):
            content = section.get("content") or ""
            assert "高并发经验扎实" not in content  # 这是别的测试里的内容
            assert "{{" not in content
