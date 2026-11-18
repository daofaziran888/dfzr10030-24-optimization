"""采集日志业务规则：状态流转、字段校验与筛选口径都收在这里。

顺着调度服务调用链，本服务承担三件事：
- 按团队计划快照记录排期操作日志，不在快照内的计划操作被拒绝并留痕（越权拒绝）；
- 同步写入团队计划数据快照并生成待审核工作台项；
- 复核快照批次（每批仅一份有效结果），把可用状态同步回采集调度。
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from app.security import RequestContext
from app.services.team_snapshot import TeamSnapshotService
from app.store import store

MODULE = "log_viewer"
LIST_FIELDS = ["日志编号", "关联任务", "日志级别", "记录时间", "日志内容", "错误堆栈", "处理状态", "处理人", "日志类型", "团队编号"]
REQUIRED_FIELDS = ["日志编号", "关联任务", "日志级别"]
STATUS_ORDER = ["未处理", "处理中", "已处理", "已忽略"]
ACTION_RULES = {"开始处理": "处理中", "标记已处理": "已处理", "忽略日志": "已忽略"}
NEGATIVE_ACTIONS = ["忽略日志"]

# 排期链路日志类型：采集运行（业务自记录）、排期操作、越权拒绝、归属变更、快照复核
EVENT_TYPES = ("采集运行", "排期操作", "越权拒绝", "归属变更", "快照复核")
REVIEW_CONCLUSIONS = ("可用", "不可用")

snapshots = TeamSnapshotService()


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


class LogViewerService:
    def list_entries(
        self,
        *,
        filters: dict[str, str] | None = None,
        keyword: str | None = None,
        status: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = store.rows(MODULE)
        for field, expected in (filters or {}).items():
            rows = [row for row in rows if expected.casefold() in str(row.get(field, "")).casefold()]
        if keyword:
            rows = [row for row in rows if keyword.casefold() in str(row.get("日志编号", "")).casefold()]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        total = len(rows)
        start = (page - 1) * size
        return rows[start:start + size], total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        return store.find(MODULE, entry_id)

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return None, missing
        rows = store.rows(MODULE)
        entry = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
        entry.update({field: values.get(field) for field in LIST_FIELDS})
        entry["日志类型"] = str(values.get("日志类型") or "采集运行")
        entry["团队编号"] = str(values.get("团队编号") or "TEAM-DAY")
        entry["status"] = STATUS_ORDER[0]
        entry["pending"] = True
        entry["abnormal"] = False
        rows.append(entry)
        return entry, []

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"日志条目 {entry_id} 不存在或已归档"
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于采集日志可执行范围"
        target = ACTION_RULES[action]
        if target not in STATUS_ORDER:
            return None, f"目标状态「{target}」不在允许的状态序列里"
        entry["status"] = target
        entry["pending"] = target != STATUS_ORDER[-1]
        entry["abnormal"] = action in NEGATIVE_ACTIONS
        return entry, f"日志条目已{action}"

    # ---------- 排期链路：操作日志与越权拒绝留痕 ----------
    def record_schedule_event(
        self,
        *,
        event_type: str,
        level: str,
        team_code: str,
        plan_code: str,
        content: str,
        operator: str,
        snapshot_version: int | None = None,
    ) -> dict[str, Any]:
        """按团队计划快照记录排期操作日志；越权拒绝同样落库，日志页保留记录。"""
        rows = store.rows(MODULE)
        entry_id = max((int(row.get("id", 0)) for row in rows), default=0) + 1
        entry = {
            "id": entry_id,
            "日志编号": f"LOG_{entry_id:04d}",
            "关联任务": plan_code,
            "日志级别": level,
            "记录时间": _now(),
            "日志内容": content,
            "错误堆栈": "",
            "处理状态": "已记录",
            "处理人": operator,
            "status": "已处理",
            "pending": False,
            "abnormal": event_type == "越权拒绝",
            "日志类型": event_type if event_type in EVENT_TYPES else "采集运行",
            "团队编号": team_code,
            "快照版本": snapshot_version,
        }
        rows.append(entry)
        return entry

    # ---------- 待审核工作台与快照复核 ----------
    def workbench(self, status: str | None = "待审核") -> list[dict[str, Any]]:
        return snapshots.workbench_items(status)

    def review_snapshot(
        self, batch_no: str, conclusion: str, ctx: RequestContext
    ) -> tuple[bool, str]:
        """复核团队计划数据快照：每批仅一份有效结果，结论同步给采集调度。"""
        if ctx.readonly:
            return False, "只读用户不能改动团队计划数据快照"
        if conclusion not in REVIEW_CONCLUSIONS:
            return False, f"复核结论只能是：{'、'.join(REVIEW_CONCLUSIONS)}"
        batch, message = snapshots.review(batch_no, conclusion, ctx.operator)
        if batch is None:
            return False, message
        self.record_schedule_event(
            event_type="快照复核",
            level="信息",
            team_code=str(batch.get("团队编号", "")),
            plan_code=batch_no,
            content=f"{message}，可用状态已同步给采集调度",
            operator=ctx.operator,
            snapshot_version=int(batch.get("快照版本", 0)),
        )
        return True, message
