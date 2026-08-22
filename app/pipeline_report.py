"""
报告生成模块 - 精简版，硬需求用 tag 格式
"""
from datetime import datetime


def generate_markdown_report(scored: list[dict], mode: str = "100") -> str:
    """生成精简的 Markdown 报告"""
    valid = [r for r in scored if "_error" not in r]
    errored = [r for r in scored if "_error" in r]
    valid.sort(key=lambda x: x.get("_score", x.get("score100", x.get("analysis", {})).get("total_score", 0)), reverse=True)

    lines = [
        "# 简历AI评分报告",
        "",
        f"评分时间: {datetime.now().strftime('%Y-%m-%d %H:%M')} | 模式: {mode}分制 | 共 {len(scored)} 份，有效 {len(valid)} 份",
        "",
    ]

    # 评分失败的简历
    if errored:
        lines.append("## ⚠️ 评分失败")
        for r in errored:
            name = r.get("name", r.get("title", "?"))
            error = r.get("_error", "未知错误")
            short_err = error[:120] + "..." if len(error) > 120 else error
            lines.append(f"- **{name}**: {short_err}")
        lines.append("")

    for i, r in enumerate(valid, 1):
        name = r.get("name", r.get("title", f"候选人{i}"))
        lines.append(f"## {i}. {name}")

        if mode == "100":
            s = r.get("score100", {})
            total = s.get("total_score", 0)
            passed = "✅通过" if s.get("pass") else "❌不及格"
            rec = s.get("recommendation", "")
            lines.append(f"**{total}分 {passed}** | {rec}")

            # 各维度一行
            scores = s.get("scores", {})
            dims = []
            for dim_name, dim_key in [("学历", "education"), ("经验", "experience"),
                                       ("硬技能", "hard_skills"), ("软技能", "soft_skills"),
                                       ("项目", "projects"), ("综合", "overall")]:
                dim = scores.get(dim_key, {})
                dims.append(f"{dim_name}{dim.get('score', '?')}/{dim.get('max', '?')}")
            lines.append(" | ".join(dims))

            # 硬需求检查 - 用 ✓/✗ 标记，前端会渲染为红绿 tag
            hard_reqs = s.get("hard_requirements_met", [])
            if hard_reqs:
                for req in hard_reqs:
                    label = req.get("requirement", "")
                    detail = req.get("detail", "")
                    if req.get("met"):
                        lines.append(f"- ✓ {label}")
                    else:
                        lines.append(f"- ✗ {label}: {detail}")

            # 亮点和风险
            highlights = s.get("highlights", [])
            concerns = s.get("concerns", [])
            if highlights:
                lines.append(f"**亮点**: {' | '.join(highlights)}")
            if concerns:
                lines.append(f"**风险**: {' | '.join(concerns)}")

            summary = s.get("summary", "")
            if summary:
                lines.append(f"> {summary}")

            # CoT 推理过程（如果有）
            thinking = s.get("_thinking", "")
            if thinking:
                # 截取关键部分，避免报告过长
                thinking_short = thinking[:500] + "..." if len(thinking) > 500 else thinking
                lines.append(f"<details><summary>🧠 推理过程</summary>\n\n```\n{thinking_short}\n```\n</details>")
        else:
            a = r.get("analysis", {})
            lines.append(f"**{a.get('total_score', '?')}/10** | {a.get('recommendation', '?')}")
            highlights = a.get("highlights", [])
            concerns = a.get("concerns", [])
            if highlights:
                lines.append(f"**亮点**: {' | '.join(highlights)}")
            if concerns:
                lines.append(f"**风险**: {' | '.join(concerns)}")
            summary = a.get("summary", "")
            if summary:
                lines.append(f"> {summary}")

        url = r.get("detail_url", "")
        if url:
            lines.append(f"[🔗 查看详情]({url})")
        lines.append("")

    return "\n".join(lines)
