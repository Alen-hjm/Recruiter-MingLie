from flask import Blueprint, render_template

web = Blueprint("web", __name__)

PAGES = {"dashboard":"仪表盘", "agent":"AI 猎头 Agent", "scrape":"简历抓取", "score":"AI 评分", "candidates":"候选人管理", "report":"评分报告", "jobs":"岗位管理与漏斗", "interview":"面试管理", "logs":"运行日志", "settings":"设置"}

@web.get("/")
def dashboard(): return render_template("workbench.html", page="dashboard", title=PAGES["dashboard"])

@web.get("/<page>")
def page(page):
    if page not in PAGES: return "Not found", 404
    return render_template("workbench.html", page=page, title=PAGES[page])

@web.get("/jobs/<int:job_id>")
def job_detail(job_id): return render_template("workbench.html", page="jobs", title=PAGES["jobs"], job_id=job_id)
