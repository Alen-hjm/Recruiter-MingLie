# -*- coding: utf-8 -*-
"""
访问令牌管理接口（网页端使用，走会话登录）。

用户流程：
    设置页 →「生成扩展令牌」→ 复制明文 → 粘贴到 Chrome 扩展设置里。

明文只在生成时返回一次；列表接口只给提示串，避免「打开设置页就能看到全部令牌」。
"""

from flask import Blueprint, current_app, jsonify, request
from flask_login import current_user, login_required

from .. import config
from ..auth import create_token, list_tokens, revoke_all_tokens, revoke_token

bp = Blueprint("api_tokens", __name__, url_prefix="/api/tokens")


@bp.route("", methods=["GET"])
@login_required
def api_list_tokens():
    return jsonify({
        "success": True,
        "data": list_tokens(current_user.id),
        "legacy_key_auth": config.ALLOW_LEGACY_KEY_AUTH,
        "max_tokens": config.MAX_TOKENS_PER_USER,
    })


@bp.route("", methods=["POST"])
@login_required
def api_create_token():
    data = request.json or {}
    label = (data.get("label") or "").strip() or "Chrome 扩展"

    token = create_token(current_user.id, label)
    return jsonify({
        "success": True,
        "token": token,
        # 明文只在这里出现一次，前端必须提示用户立刻保存
        "warning": "该令牌仅显示一次，请立即复制保存。之后只能撤销重建。",
        "header": "X-API-Token",
    })


@bp.route("/<int:token_id>", methods=["DELETE"])
@login_required
def api_revoke_token(token_id):
    if revoke_token(current_user.id, token_id):
        return jsonify({"success": True})
    return jsonify({"success": False, "error": "令牌不存在或已撤销"}), 404


@bp.route("/revoke-all", methods=["POST"])
@login_required
def api_revoke_all():
    count = revoke_all_tokens(current_user.id)
    return jsonify({"success": True, "revoked": count})
