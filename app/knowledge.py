"""
知识库模块 - 读取 Obsidian 笔记作为 AI 评分的知识背景
"""
import os
import re
import json
from pathlib import Path
from typing import Optional


def parse_frontmatter(content: str) -> tuple[dict, str]:
    """解析 YAML frontmatter 和正文"""
    if not content.startswith("---"):
        return {}, content

    parts = content.split("---", 2)
    if len(parts) < 3:
        return {}, content

    frontmatter_str = parts[1].strip()
    body = parts[2].strip()

    # 简单解析 YAML（不依赖 pyyaml）
    fm = {}
    current_key = None
    current_list = None
    current_multiline = None
    multiline_key = None

    for line in frontmatter_str.split("\n"):
        line_stripped = line.strip()

        # 多行字符串结束
        if current_multiline is not None:
            if line.startswith("  ") or line.startswith("\t"):
                current_multiline += " " + line_stripped
                continue
            else:
                fm[multiline_key] = current_multiline.strip().strip('"')
                current_multiline = None
                multiline_key = None

        # 列表项
        if line_stripped.startswith("- ") and current_key:
            if current_list is None:
                current_list = []
            current_list.append(line_stripped[2:].strip().strip('"'))
            fm[current_key] = current_list
            continue

        # 键值对
        if ":" in line_stripped and not line_stripped.startswith("-"):
            key, _, value = line_stripped.partition(":")
            key = key.strip()
            value = value.strip()

            current_key = key
            current_list = None

            if not value:
                # 可能是多行
                current_multiline = ""
                multiline_key = key
                continue

            # 去掉引号
            value = value.strip('"').strip("'")

            # 尝试解析数字
            try:
                value = int(value)
            except ValueError:
                try:
                    value = float(value)
                except ValueError:
                    pass

            fm[key] = value
            continue

    # 处理最后的多行
    if current_multiline is not None:
        fm[multiline_key] = current_multiline.strip().strip('"')

    return fm, body


def scan_knowledge_base(kb_path: str) -> list[dict]:
    """扫描知识库，返回所有笔记的 frontmatter 和路径"""
    notes = []
    # 规范化路径
    kb_path = kb_path.replace('\\', '/')
    kb = Path(kb_path)
    
    # 如果路径不存在，尝试展开用户目录
    if not kb.exists():
        kb = Path(os.path.expanduser(kb_path))
    
    if not kb.exists():
        print(f"[知识库] 路径不存在: {kb_path}")
        return notes

    for md_file in kb.rglob("*.md"):
        try:
            content = md_file.read_text(encoding="utf-8")
            fm, body = parse_frontmatter(content)
            if fm:  # 只收录有 frontmatter 的笔记
                notes.append({
                    "path": str(md_file),
                    "filename": md_file.stem,
                    "folder": md_file.parent.name,
                    "frontmatter": fm,
                    "body": body[:2000],  # 截取正文前2000字
                })
        except Exception:
            continue

    return notes


def find_relevant_notes(notes: list[dict], job_name: str, job_description: str = "") -> list[dict]:
    """根据岗位名称和描述，找到最相关的知识库笔记"""
    # 构建搜索词
    search_text = f"{job_name} {job_description}".lower()
    search_words = set(re.findall(r'[\u4e00-\u9fff]+|[a-zA-Z]+', search_text))

    scored = []
    for note in notes:
        score = 0
        fm = note["frontmatter"]

        # 匹配 typical_positions（权重最高）
        positions = fm.get("typical_positions", [])
        if isinstance(positions, list):
            for pos in positions:
                if pos.lower() in search_text or search_text in pos.lower():
                    score += 10
                    break

        # 匹配 keywords
        keywords = fm.get("keywords", [])
        if isinstance(keywords, list):
            for kw in keywords:
                if kw.lower() in search_text:
                    score += 2

        # 匹配文件名
        if note["filename"].lower() in search_text or any(w in note["filename"].lower() for w in search_words if len(w) > 1):
            score += 5

        # 匹配文件夹名
        folder = note["folder"].lower()
        if any(w in folder for w in search_words if len(w) > 1):
            score += 1

        if score > 0:
            scored.append((score, note))

    scored.sort(key=lambda x: x[0], reverse=True)
    return [note for _, note in scored[:5]]  # 最多返回5篇


def build_knowledge_context(relevant_notes: list[dict]) -> str:
    """将相关笔记构建为评分 prompt 的上下文"""
    if not relevant_notes:
        return ""

    sections = []
    for note in relevant_notes:
        fm = note["frontmatter"]
        parts = [f"【{note['filename']}】"]

        # 评分指南
        guidelines = fm.get("scoring_guidelines", "")
        if guidelines:
            parts.append(f"评分要点：{guidelines}")

        # 自定义权重
        weights = fm.get("skill_weights", {})
        if weights and isinstance(weights, dict):
            w_str = "、".join(f"{k}({v}分)" for k, v in weights.items() if isinstance(v, (int, float)))
            if w_str:
                parts.append(f"建议权重：{w_str}")

        # 硬需求模板
        hard_reqs = fm.get("hard_requirement_templates", [])
        if hard_reqs and isinstance(hard_reqs, list):
            parts.append("硬需求参考：" + "；".join(hard_reqs))

        # 风险点
        pitfalls = fm.get("anti_pitfalls", [])
        if pitfalls and isinstance(pitfalls, list):
            parts.append("简历风险点：" + "；".join(pitfalls[:5]))

        # 核心技能
        skills = fm.get("core_skill_patterns", [])
        if skills and isinstance(skills, list):
            parts.append("核心技能：" + "、".join(skills[:8]))

        sections.append("\n".join(parts))

    return "\n\n---\n\n".join(sections)


def read_job_note(job_folder: str, job_name: str) -> Optional[dict]:
    """读取 Obsidian 岗位文件夹中匹配的岗位笔记"""
    # 规范化路径
    job_folder = job_folder.replace('\\', '/')
    folder = Path(job_folder)
    
    # 如果路径不存在，尝试展开用户目录
    if not folder.exists():
        folder = Path(os.path.expanduser(job_folder))
    
    if not folder.exists():
        return None

    for md_file in folder.rglob("*.md"):
        if job_name.lower() in md_file.stem.lower():
            try:
                content = md_file.read_text(encoding="utf-8")
                fm, body = parse_frontmatter(content)
                return {
                    "path": str(md_file),
                    "filename": md_file.stem,
                    "frontmatter": fm,
                    "body": body,
                }
            except Exception:
                continue
    return None


def append_to_job_note(job_folder: str, job_name: str, section_title: str, content: str) -> bool:
    """向 Obsidian 岗位笔记追加内容（如面试反馈）"""
    # 规范化路径
    job_folder = job_folder.replace('\\', '/')
    folder = Path(job_folder)
    
    # 如果路径不存在，尝试展开用户目录
    if not folder.exists():
        folder = Path(os.path.expanduser(job_folder))
    
    if not folder.exists():
        try:
            folder.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            print(f"[知识库] 创建目录失败: {e}")
            return False

    # 查找匹配的笔记
    target_file = None
    for md_file in folder.rglob("*.md"):
        if job_name.lower() in md_file.stem.lower():
            target_file = md_file
            break

    if not target_file:
        # 创建新笔记
        target_file = folder / f"{job_name}.md"
        target_file.write_text(f"# {job_name}\n\n", encoding="utf-8")

    # 追加内容
    try:
        existing = target_file.read_text(encoding="utf-8")
        append_text = f"\n\n## {section_title}\n\n{content}\n"
        target_file.write_text(existing + append_text, encoding="utf-8")
        return True
    except Exception as e:
        print(f"[知识库] 写入失败: {e}")
        return False


def sync_job_to_obsidian(job_folder: str, job_data: dict) -> bool:
    """将网站岗位信息同步到 Obsidian 岗位笔记"""
    # 规范化路径
    job_folder = job_folder.replace('\\', '/')
    folder = Path(job_folder)
    
    # 如果路径不存在，尝试展开用户目录
    if not folder.exists():
        folder = Path(os.path.expanduser(job_folder))
    
    if not folder.exists():
        try:
            folder.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            print(f"[知识库] 创建目录失败: {e}")
            return False

    job_name = job_data.get("name", "")
    if not job_name:
        return False

    # 查找或创建笔记
    target_file = None
    for md_file in folder.rglob("*.md"):
        if job_name.lower() in md_file.stem.lower():
            target_file = md_file
            break

    if not target_file:
        target_file = folder / f"{job_name}.md"

    # 构建笔记内容
    keyword = job_data.get("keyword", "")
    jd = job_data.get("job_description", "")
    tags = job_data.get("tags", [])

    frontmatter_lines = [
        "---",
        f"name: {job_name}",
        f"keyword: {keyword}",
        f"last_synced: {__import__('datetime').datetime.now().strftime('%Y-%m-%d %H:%M')}",
    ]
    if tags:
        frontmatter_lines.append("tags:")
        for tag in tags:
            frontmatter_lines.append(f"  - {tag}")
    frontmatter_lines.append("---")

    body_lines = [
        f"# {job_name}",
        "",
        "## 岗位描述",
        "",
        jd or "（暂未填写）",
        "",
        "## 面试反馈",
        "",
        "（面试淘汰原因将自动追加到此处）",
        "",
    ]

    content = "\n".join(frontmatter_lines) + "\n\n" + "\n".join(body_lines)

    try:
        target_file.write_text(content, encoding="utf-8")
        return True
    except Exception as e:
        print(f"[知识库] 同步岗位失败: {e}")
        return False


def sync_pipeline_to_obsidian(job_folder: str, job_name: str, candidates_by_stage: dict, summary: dict) -> bool:
    """将候选人管道状态同步到 Obsidian 岗位笔记
    
    在岗位笔记中更新「候选人管道」章节，记录每个阶段的候选人和淘汰原因。
    这样每次状态变更都会追加到 Obsidian，形成完整的招聘过程记录。
    """
    from datetime import datetime

    job_folder = job_folder.replace('\\', '/')
    folder = Path(job_folder)
    if not folder.exists():
        folder = Path(os.path.expanduser(job_folder))
    if not folder.exists():
        try:
            folder.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            print(f"[知识库] 创建目录失败: {e}")
            return False

    # 查找或创建岗位笔记
    target_file = None
    for md_file in folder.rglob("*.md"):
        if job_name.lower() in md_file.stem.lower():
            target_file = md_file
            break
    if not target_file:
        target_file = folder / f"{job_name}.md"
        target_file.write_text(f"# {job_name}\n\n", encoding="utf-8")

    # 构建管道章节
    now = datetime.now().strftime('%Y-%m-%d %H:%M')
    lines = [f"## 候选人管道（{now} 更新）\n"]

    stage_labels = {
        "recommended": "🟢 AI推荐",
        "contacted": "📞 已电话联系",
        "submitted": "📤 已推荐给甲方",
        "interview": "🎤 进入面试",
        "offer": "📋 发了Offer",
        "hired": "✅ 已录用",
        "rejected": "❌ 已淘汰",
    }

    # 摘要
    lines.append(f"**总计: {summary.get('total', 0)} 人**\n")
    for stage_key, label in stage_labels.items():
        count = summary.get(stage_key, 0)
        if count > 0:
            lines.append(f"- {label}: {count}人")
    lines.append("")

    # 各阶段详情
    for stage_key, label in stage_labels.items():
        candidates = candidates_by_stage.get(stage_key, [])
        if not candidates:
            continue
        lines.append(f"### {label}\n")
        for c in candidates:
            name = c.get('candidate_name', '未知')
            score = c.get('ai_score', '-')
            reason = c.get('reason', '')
            reject_stage = c.get('reject_stage', '')
            company = c.get('company_rejected', '')
            updated = c.get('updated_at', c.get('created_at', ''))

            entry = f"- **{name}**（AI评分: {score}分）"
            if stage_key == 'rejected':
                stage_labels_map = {
                    'phone_screen': '电话筛选',
                    'client_reject': '甲方淘汰',
                    'interview_reject': '面试淘汰',
                    'offer_declined': '拒Offer',
                }
                reject_label = stage_labels_map.get(reject_stage, reject_stage)
                entry += f" | 淘汰阶段: {reject_label}"
                if company:
                    entry += f" | 甲方: {company}"
                if reason:
                    entry += f" | 原因: {reason}"
            elif reason and reason != 'AI评分推荐':
                entry += f" | 备注: {reason}"
            if updated:
                entry += f" | {updated[:10]}"
            lines.append(entry)
        lines.append("")

    lines.append(f"---\n*同步时间：{now}*\n")
    new_section = "\n".join(lines)

    # 追加到笔记（替换旧的管道章节）
    try:
        existing = target_file.read_text(encoding="utf-8")
        # 如果已有管道章节，替换它
        if "## 候选人管道" in existing:
            import re
            pattern = r"## 候选人管道.*?(?=\n## |\Z)"
            existing = re.sub(pattern, new_section + "\n", existing, flags=re.DOTALL)
        else:
            existing += "\n\n" + new_section
        target_file.write_text(existing, encoding="utf-8")
        return True
    except Exception as e:
        print(f"[知识库] 同步管道失败: {e}")
        return False
