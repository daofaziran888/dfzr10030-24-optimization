"""采集调度业务规则：状态流转、字段校验与筛选口径都收在这里。

读取口径从全表遍历改为团队计划数据快照：列表与详情只落在当前归属团队的
快照条目上，按团队排期范围减少越权读取和重复扫描。计划绑定团队和状态，
归属变更后旧团队不能继续读取详情；排期操作经采集日志服务留痕并同步快照。
"""
from __future__ import annotations

from typing import Any

from app.schemas import HandoverPayload
from app.security import RequestContext
from app.services.log_viewer import LogViewerService
from app.services.team_snapshot import HANDOVER_ITEM, TeamSnapshotService
from app.store import store

MODULE = "schedule"
LIST_FIELDS = ["计划编号", "关联任务", "调度周期", "开始时间", "结束时间", "依赖任务", "失败策略", "计划状态"]
REQUIRED_FIELDS = ["计划编号", "关联任务", "调度周期"]
STATUS_ORDER = ["草稿", "已排期", "运行中", "已完成", "已暂停"]
ACTION_RULES = {"确认排期": "已排期", "启动调度": "运行中", "暂停调度": "已暂停"}
NEGATIVE_ACTIONS = []

# 共享计划列表里，他团队计划的敏感字段打码：看得到计划存在，看不到排期细节
MASKED_FIELDS = {"依赖任务", "失败策略"}
MASKED_VALUE = "***"


class ScheduleService:
    def __init__(self) -> None:
        self.snapshots = TeamSnapshotService()
        self.logs = LogViewerService()

    # ---------- 列表：按团队快照受控读取 ----------
    def list_entries(
        self,
        *,
        ctx: RequestContext,
        filters: dict[str, str] | None = None,
        keyword: str | None = None,
        status: str | None = None,
        shared: bool = False,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int, dict[str, Any]]:
        snapshot = self.snapshots.ensure_snapshot(ctx.team_code)
        allowed_ids = {int(item["id"]) for item in snapshot["计划条目"]}
        # 只扫描本团队快照内的计划，不再遍历全部团队计划
        rows: list[dict[str, Any]] = []
        for plan_id in sorted(allowed_ids):
            row = store.find(MODULE, plan_id)
            if row is not None:
                rows.append({**row, "共享标记": "本团队"})
        if shared:
            # 白班夜班共享计划列表：他团队计划只给概要，敏感字段打码
            for row in store.rows(MODULE):
                if int(row.get("id", 0)) in allowed_ids:
                    continue
                masked = {
                    key: (MASKED_VALUE if key in MASKED_FIELDS else value)
                    for key, value in row.items()
                }
                masked["共享标记"] = "其他团队"
                rows.append(masked)
        for field, expected in (filters or {}).items():
            rows = [row for row in rows if expected.casefold() in str(row.get(field, "")).casefold()]
        if keyword:
            rows = [row for row in rows if keyword.casefold() in str(row.get("计划编号", "")).casefold()]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        total = len(rows)
        start = (page - 1) * size
        return rows[start:start + size], total, self.snapshot_meta(snapshot)

    def snapshot_meta(self, snapshot: dict[str, Any]) -> dict[str, Any]:
        return {
            "快照编号": snapshot.get("快照编号", ""),
            "团队编号": snapshot.get("团队编号", ""),
            "排期范围": snapshot.get("排期范围", ""),
            "版本": snapshot.get("版本", 0),
            "核对状态": snapshot.get("核对状态", ""),
            "可用状态": snapshot.get("可用状态", ""),
            "批次号": snapshot.get("批次号", ""),
            "计划数": len(snapshot.get("计划条目", [])),
            "历史版本": self.snapshots.snapshot_versions(str(snapshot.get("团队编号", ""))),
        }

    # ---------- 详情：仅当前归属团队可读 ----------
    def get_entry(
        self, entry_id: int, ctx: RequestContext
    ) -> tuple[dict[str, Any] | None, str, bool]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"调度计划 {entry_id} 不存在或已归档", False
        owner = str(entry.get("归属团队", ""))
        if owner != ctx.team_code:
            self.logs.record_schedule_event(
                event_type="越权拒绝",
                level="拒绝",
                team_code=ctx.team_code,
                plan_code=str(entry.get("计划编号", entry_id)),
                content=(
                    f"{self.snapshots.team_name(ctx.team_code)}尝试读取归属"
                    f"{self.snapshots.team_name(owner)}的计划详情，已拦截"
                ),
                operator=ctx.operator,
                snapshot_version=int(entry.get("归属版本", 0)),
            )
            return None, f"计划归属{self.snapshots.team_name(owner)}，详情仅对当前归属团队开放", True
        return entry, "ok", False

    # ---------- 登记：计划绑定团队和状态 ----------
    def create_entry(
        self, values: dict[str, Any], ctx: RequestContext
    ) -> tuple[dict[str, Any] | None, str]:
        if ctx.readonly:
            self.logs.record_schedule_event(
                event_type="越权拒绝",
                level="拒绝",
                team_code=ctx.team_code,
                plan_code=str(values.get("计划编号") or "-"),
                content="只读用户尝试登记调度计划，已拦截",
                operator=ctx.operator,
            )
            return None, "只读用户不能改动团队计划数据快照"
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return None, f"缺少必填字段：{'、'.join(missing)}"
        rows = store.rows(MODULE)
        entry = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
        entry.update({field: values.get(field) for field in LIST_FIELDS})
        entry["status"] = STATUS_ORDER[0]
        entry["pending"] = True
        entry["abnormal"] = False
        entry["归属团队"] = ctx.team_code
        entry["归属版本"] = self.snapshots.current_version()
        entry["排期范围"] = self.snapshots.team_range(ctx.team_code)
        rows.append(entry)
        snapshot = self.snapshots.register_plan(entry)
        self.logs.record_schedule_event(
            event_type="排期操作",
            level="信息",
            team_code=ctx.team_code,
            plan_code=str(entry.get("计划编号", "")),
            content=(
                f"{ctx.operator}登记调度计划{entry.get('计划编号', '')}，"
                "已同步写入团队计划数据快照并生成待审核工作台项"
            ),
            operator=ctx.operator,
            snapshot_version=int(snapshot.get("版本", 0)),
        )
        return entry, ""

    # ---------- 动作：拒绝不在快照内的计划操作 ----------
    def run_action(
        self, entry_id: int, action: str, ctx: RequestContext
    ) -> tuple[dict[str, Any] | None, str]:
        if ctx.readonly:
            self.logs.record_schedule_event(
                event_type="越权拒绝",
                level="拒绝",
                team_code=ctx.team_code,
                plan_code=str(entry_id),
                content=f"只读用户尝试执行调度动作「{action}」，已拦截",
                operator=ctx.operator,
            )
            return None, "只读用户不能改动团队计划数据快照"
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"调度计划 {entry_id} 不存在或已归档"
        snapshot = self.snapshots.ensure_snapshot(ctx.team_code)
        in_snapshot = any(int(item["id"]) == entry_id for item in snapshot["计划条目"])
        if not in_snapshot or entry.get("归属团队") != ctx.team_code:
            self.logs.record_schedule_event(
                event_type="越权拒绝",
                level="拒绝",
                team_code=ctx.team_code,
                plan_code=str(entry.get("计划编号", entry_id)),
                content=(
                    f"{self.snapshots.team_name(ctx.team_code)}对不在本团队快照内的计划"
                    f"执行「{action}」，已拒绝"
                ),
                operator=ctx.operator,
                snapshot_version=int(snapshot.get("版本", 0)),
            )
            return None, "计划不在当前团队快照内，排期操作被拒绝"
        if snapshot.get("可用状态") == "不可用":
            return None, "快照核对结论为不可用，调度动作已暂停，请先在采集日志页完成复核"
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于采集调度可执行范围"
        target = ACTION_RULES[action]
        if target not in STATUS_ORDER:
            return None, f"目标状态「{target}」不在允许的状态序列里"
        entry["status"] = target
        entry["计划状态"] = target
        entry["pending"] = target != STATUS_ORDER[-1]
        entry["abnormal"] = action in NEGATIVE_ACTIONS
        # 采集日志服务同步写入团队计划数据快照
        self.snapshots.sync_plan_status(entry_id, ctx.team_code, target)
        self.logs.record_schedule_event(
            event_type="排期操作",
            level="信息",
            team_code=ctx.team_code,
            plan_code=str(entry.get("计划编号", entry_id)),
            content=f"{ctx.operator}执行「{action}」，计划状态流转为{target}，已同步写入团队计划快照",
            operator=ctx.operator,
            snapshot_version=int(snapshot.get("版本", 0)),
        )
        return entry, f"调度计划已{action}"

    # ---------- 移交：整体刷新 / 逐条撤销 ----------
    def handover(
        self, payload: HandoverPayload, ctx: RequestContext
    ) -> tuple[bool, str, dict[str, Any]]:
        if ctx.readonly:
            self.logs.record_schedule_event(
                event_type="越权拒绝",
                level="拒绝",
                team_code=ctx.team_code,
                plan_code="-",
                content="只读用户尝试发起计划移交，已拦截",
                operator=ctx.operator,
            )
            return False, "只读用户不能改动团队计划数据快照", {}
        from_team = (payload.from_team or ctx.team_code).strip()
        if from_team != ctx.team_code:
            self.logs.record_schedule_event(
                event_type="越权拒绝",
                level="拒绝",
                team_code=ctx.team_code,
                plan_code="-",
                content=(
                    f"{self.snapshots.team_name(ctx.team_code)}尝试移交"
                    f"{self.snapshots.team_name(from_team)}的计划，越权修改已被拦截"
                ),
                operator=ctx.operator,
            )
            return False, "只能移交本团队归属的计划，越权修改已被拦截", {}
        ok, message, summary = self.snapshots.handover(
            from_team=from_team,
            to_team=payload.to_team.strip(),
            mode=payload.mode.strip(),
            plan_ids=payload.plan_ids if payload.mode.strip() == HANDOVER_ITEM else None,
            base_version=payload.base_version,
            operator=ctx.operator,
        )
        if ok:
            self.logs.record_schedule_event(
                event_type="归属变更",
                level="信息",
                team_code=ctx.team_code,
                plan_code="、".join(summary.get("moved", [])),
                content=message,
                operator=ctx.operator,
                snapshot_version=int(summary.get("version", 0)),
            )
        return ok, message, summary
