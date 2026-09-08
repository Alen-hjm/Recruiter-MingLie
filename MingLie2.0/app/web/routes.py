from flask import Blueprint, render_template

web = Blueprint("web", __name__)

@web.get("/")
def dashboard(): return render_template("dashboard.html")

@web.get("/jobs/<int:job_id>")
def job_detail(job_id): return render_template("job.html", job_id=job_id)
