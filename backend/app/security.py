"""请求上下文：团队归属与角色判定。

团队计划数据快照处于受控与共享范围：所有读取按团队归属收敛，
所有改动按角色拦截（只读用户不能改动）。上下文从请求头解析，
前端会话切换值班团队或角色时随请求带上。
"""
from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import unquote

from fastapi import Request

DEFAULT_TEAM = "TEAM-DAY"
OPERATOR_ROLE = "operator"
READONLY_ROLE = "readonly"


@dataclass(frozen=True)
class RequestContext:
    team_code: str
    role: str
    operator: str

    @property
    def readonly(self) -> bool:
        """只读用户不能改动团队计划数据快照及其关联数据。"""
        return self.role == READONLY_ROLE


def get_context(request: Request) -> RequestContext:
    team = (
        request.headers.get("x-team-code")
        or request.query_params.get("team")
        or DEFAULT_TEAM
    ).strip() or DEFAULT_TEAM
    role = (request.headers.get("x-operator-role") or OPERATOR_ROLE).strip() or OPERATOR_ROLE
    # 前端对中文操作人名做了 URL 编码（请求头只能是 latin-1），这里解码还原
    operator = unquote(request.headers.get("x-operator") or "值班管理员").strip() or "值班管理员"
    return RequestContext(team_code=team, role=role, operator=operator)
