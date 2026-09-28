# -*- coding: utf-8 -*-
"""页面路由：13 个工作台页面，全部只做模板渲染。"""

from flask import Blueprint, redirect, render_template, request, flash, url_for
from flask_login import current_user, login_required, login_user, logout_user

from ..models import authenticate_user, create_user

bp = Blueprint("pages", __name__)


@bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("pages.dashboard"))
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        user = authenticate_user(username, password)
        if user:
            login_user(user)
            return redirect(url_for("pages.dashboard"))
        flash("用户名或密码错误")
    return render_template("login.html")


@bp.route("/register", methods=["GET", "POST"])
def register():
    if current_user.is_authenticated:
        return redirect(url_for("pages.dashboard"))
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        display_name = request.form.get("display_name", "").strip()
        if not username or not password:
            flash("用户名和密码不能为空")
        elif len(password) < 8:
            # 原来是 4 位。这是一个能直接抓取真实简历的系统，门槛不该这么低。
            flash("密码至少 8 位")
        else:
            try:
                user = create_user(username, password, display_name)
                login_user(user)
                return redirect(url_for("pages.dashboard"))
            except Exception:
                flash("用户名已存在")
    return render_template("register.html")


@bp.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("pages.login"))


@bp.route("/")
@login_required
def dashboard():
    return render_template("dashboard.html")


@bp.route("/scrape")
@login_required
def scrape_page():
    return render_template("scrape.html")


@bp.route("/score")
@login_required
def score_page():
    return render_template("score.html")


@bp.route("/report")
@login_required
def report_page():
    return render_template("report.html")


@bp.route("/jobs")
@login_required
def jobs_page():
    return render_template("jobs.html")


@bp.route("/settings")
@login_required
def settings_page():
    return render_template("settings.html")


@bp.route("/interview")
@login_required
def interview_page():
    return render_template("interview.html")


@bp.route("/candidates")
@login_required
def candidates_page():
    return render_template("candidates.html")


@bp.route("/agent")
@login_required
def agent_page():
    return render_template("agent.html")


@bp.route("/logs")
@login_required
def logs_page():
    return render_template("logs.html")
