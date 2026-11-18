"""团队计划数据快照：按排期归属汇总调度计划，支撑受控读取、复核与移交。

设计口径：
- 输入是调度计划归属数据（计划上的归属团队/归属版本/排期范围）与团队排期数据
  （team_shift），按团队范围汇总成快照，采集调度的列表与详情只读本团队当前快照，
  不再全表扫描，越权读取与重复扫描在这一层被消除。
- 同一团队同一排期范围内结果稳定：快照落地后就是读取口径，他团队的变更不会混入。
- 移交按版本边界一次生效：请求必须基于当前版本发起，过期版本直接拒绝；
  旧版本快照转入历史，支持按版本回查旧结果。
- 移交两种做法：整体刷新（快照整体按新归属生效）与逐条撤销（计划按条目归属
  逐个切换），取舍不同则生成的团队计划数据快照不同；共同约束是同一计划详情
  只能对当前归属团队开放。
- 采集日志服务同步写入快照并生成待审核工作台项；复核每批仅一份有效结果，
  核对结论（可用/不可用）写回快照并同步给采集调度。
"""
from __future__ import annotations

import threading
from datetime import datetime
from typing import Any

from app.store import store

SCHEDULE_MODULE = "schedule"
SNAPSHOT_MODULE = "team_snapshot"
TEAM_MODULE = "team_shift"
CHANGE_MODULE = "ownership_change"
BATCH_MODULE = "review_batch"
WORKBENCH_MODULE = "workbench_item"

DEFAULT_RANGE = "2026-W47"
HANDOVER_FULL = "整体刷新"
HANDOVER_ITEM = "逐条撤销"
HANDOVER_MODES = (HANDOVER_FULL, HANDOVER_ITEM)

REVIEW_PENDING = "待复核"
REVIEW_DONE = "已复核"

_lock = threading.RLock()


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _next_id(module: str) -> int:
    return max((int(row.get("id", 0)) for row in store.rows(module)), default=0) + 1


class TeamSnapshotService:
    """团队计划快照的构建、同步写入、复核、移交与回查。"""

    # ---------- 团队排期数据 ----------
    def teams(self) -> list[dict[str, Any]]:
        return list(store.rows(TEAM_MODULE))

    def team_name(self, team_code: str) -> str:
        for team in store.rows(TEAM_MODULE):
            if team.get("团队编号") == team_code:
                return str(team.get("团队名称") or team_code)
        return team_code

    def team_exists(self, team_code: str) -> bool:
        return any(team.get("团队编号") == team_code for team in store.rows(TEAM_MODULE))

    def team_range(self, team_code: str) -> str:
        for team in store.rows(TEAM_MODULE):
            if team.get("团队编号") == team_code:
                return str(team.get("排期范围") or DEFAULT_RANGE)
        return DEFAULT_RANGE

    # ---------- 版本边界 ----------
    def current_version(self) -> int:
        versions = [int(row.get("版本", 0)) for row in store.rows(SNAPSHOT_MODULE)]
        versions += [int(row.get("归属版本", 0)) for row in store.rows(SCHEDULE_MODULE)]
        return max(versions, default=1)

    # ---------- 快照构建 ----------
    def build_snapshot(self, team_code: str, version: int) -> dict[str, Any]:
        """按团队范围汇总调度计划，生成新的当前快照，旧快照转入历史可回查。"""
        schedule_range = self.team_range(team_code)
        with _lock:
            for row in store.rows(SNAPSHOT_MODULE):
                if row.get("团队编号") == team_code and row.get("生效状态") == "当前":
                    row["生效状态"] = "历史"
            entries = [
                {
                    "id": int(plan["id"]),
                    "计划编号": plan.get("计划编号", ""),
                    "计划状态": plan.get("status", ""),
                }
                for plan in store.rows(SCHEDULE_MODULE)
                if plan.get("归属团队") == team_code
                and str(plan.get("排期范围") or DEFAULT_RANGE) == schedule_range
            ]
            snapshot = {
                "id": _next_id(SNAPSHOT_MODULE),
                "快照编号": f"SNAP-{schedule_range}-{team_code.split('-')[-1]}-V{version}",
                "团队编号": team_code,
                "排期范围": schedule_range,
                "版本": version,
                "计划条目": entries,
                "核对状态": "待审核",
                "可用状态": "待核对",
                "批次号": "",
                "生成时间": _now(),
                "生效状态": "当前",
            }
            store.rows(SNAPSHOT_MODULE).append(snapshot)
            self._open_review(snapshot)
        return snapshot

    def _open_review(self, snapshot: dict[str, Any]) -> None:
        """为快照开启新的复核批次，并生成待审核工作台项。"""
        team_code = str(snapshot["团队编号"])
        version = int(snapshot["版本"])
        seq = sum(
            1
            for batch in store.rows(BATCH_MODULE)
            if batch.get("团队编号") == team_code and int(batch.get("快照版本", 0)) == version
        ) + 1
        batch_no = f"{str(snapshot['快照编号']).replace('SNAP', 'BATCH')}-R{seq}"
        snapshot["批次号"] = batch_no
        store.rows(BATCH_MODULE).append({
            "id": _next_id(BATCH_MODULE),
            "批次号": batch_no,
            "团队编号": team_code,
            "快照版本": version,
            "状态": REVIEW_PENDING,
            "有效结果": "",
            "复核人": "",
            "复核时间": "",
        })
        workbench_id = _next_id(WORKBENCH_MODULE)
        store.rows(WORKBENCH_MODULE).append({
            "id": workbench_id,
            "事项编号": f"WBI-{workbench_id:04d}",
            "类型": "快照复核",
            "团队编号": team_code,
            "快照版本": version,
            "批次号": batch_no,
            "摘要": (
                f"{self.team_name(team_code)} {snapshot['排期范围']} "
                f"团队计划快照待复核（{len(snapshot['计划条目'])} 条计划）"
            ),
            "状态": "待审核",
            "生成时间": _now(),
        })

    # ---------- 快照读取 ----------
    def current_snapshot(self, team_code: str) -> dict[str, Any] | None:
        for row in reversed(store.rows(SNAPSHOT_MODULE)):
            if row.get("团队编号") == team_code and row.get("生效状态") == "当前":
                return row
        return None

    def ensure_snapshot(self, team_code: str) -> dict[str, Any]:
        snapshot = self.current_snapshot(team_code)
        if snapshot is None:
            snapshot = self.build_snapshot(team_code, self.current_version())
        return snapshot

    def snapshot_for_version(self, team_code: str, version: int) -> dict[str, Any] | None:
        """旧结果回查：按版本取历史快照。"""
        for row in store.rows(SNAPSHOT_MODULE):
            if row.get("团队编号") == team_code and int(row.get("版本", 0)) == version:
                return row
        return None

    def snapshot_versions(self, team_code: str) -> list[int]:
        return sorted(
            {int(row.get("版本", 0)) for row in store.rows(SNAPSHOT_MODULE) if row.get("团队编号") == team_code},
            reverse=True,
        )

    # ---------- 同步写入（采集日志服务调用） ----------
    def sync_plan_status(self, plan_id: int, team_code: str, status: str) -> bool:
        """把计划状态同步写进本团队当前快照，保持快照与计划一致。"""
        snapshot = self.current_snapshot(team_code)
        if snapshot is None:
            return False
        with _lock:
            for item in snapshot["计划条目"]:
                if int(item["id"]) == plan_id:
                    item["计划状态"] = status
                    return True
        return False

    def register_plan(self, plan: dict[str, Any]) -> dict[str, Any]:
        """新计划写入所属团队当前快照；结构变更使旧核对结论失效，重新待审核。"""
        team_code = str(plan.get("归属团队", ""))
        snapshot = self.current_snapshot(team_code)
        if snapshot is None:
            return self.build_snapshot(team_code, self.current_version())
        with _lock:
            snapshot["计划条目"].append({
                "id": int(plan["id"]),
                "计划编号": plan.get("计划编号", ""),
                "计划状态": plan.get("status", ""),
            })
            snapshot["核对状态"] = "待审核"
            snapshot["可用状态"] = "待核对"
            self._open_review(snapshot)
        return snapshot

    # ---------- 复核：每批仅一份有效结果 ----------
    def review(self, batch_no: str, conclusion: str, operator: str) -> tuple[dict[str, Any] | None, str]:
        """复核快照批次：并发复核只保留先到的一份有效结果，结论写回快照。"""
        with _lock:
            batch = next(
                (row for row in store.rows(BATCH_MODULE) if row.get("批次号") == batch_no),
                None,
            )
            if batch is None:
                return None, f"复核批次 {batch_no} 不存在"
            if batch.get("状态") == REVIEW_DONE:
                return None, f"批次 {batch_no} 已存在有效复核结果，并发复核仅保留一份"
            batch["状态"] = REVIEW_DONE
            batch["有效结果"] = conclusion
            batch["复核人"] = operator
            batch["复核时间"] = _now()
            snapshot = next(
                (row for row in store.rows(SNAPSHOT_MODULE) if row.get("批次号") == batch_no),
                None,
            )
            if snapshot is not None:
                snapshot["核对状态"] = "已核对"
                snapshot["可用状态"] = conclusion
            for item in store.rows(WORKBENCH_MODULE):
                if item.get("批次号") == batch_no and item.get("状态") == "待审核":
                    item["状态"] = "已审核"
        return batch, f"批次 {batch_no} 复核完成，快照核对结论：{conclusion}"

    # ---------- 移交：版本边界一次生效 ----------
    def handover(
        self,
        *,
        from_team: str,
        to_team: str,
        mode: str,
        plan_ids: list[int] | None,
        base_version: int,
        operator: str,
    ) -> tuple[bool, str, dict[str, Any]]:
        """计划移交：整体刷新或逐条撤销，按版本边界一次生效，旧结果可回查。"""
        if to_team == from_team:
            return False, "目标团队与当前归属团队相同，无需移交", {}
        if not self.team_exists(to_team):
            return False, f"目标团队 {to_team} 不存在", {}
        if mode not in HANDOVER_MODES:
            return False, f"移交方式「{mode}」不支持，可选：{'、'.join(HANDOVER_MODES)}", {}
        with _lock:
            current = self.current_version()
            if base_version != current:
                return False, (
                    f"移交边界已推进到版本 {current}，本次提交基于版本 {base_version}，"
                    "请刷新后按新版本重新发起"
                ), {}
            owned = [plan for plan in store.rows(SCHEDULE_MODULE) if plan.get("归属团队") == from_team]
            if mode == HANDOVER_FULL:
                moving = owned
            else:
                wanted = {int(plan_id) for plan_id in (plan_ids or [])}
                moving = [plan for plan in owned if int(plan.get("id", 0)) in wanted]
            if not moving:
                return False, "没有可移交的调度计划", {}
            new_version = current + 1
            now = _now()
            for plan in moving:
                plan["归属团队"] = to_team
                plan["归属版本"] = new_version
                store.rows(CHANGE_MODULE).append({
                    "id": _next_id(CHANGE_MODULE),
                    "计划编号": plan.get("计划编号", ""),
                    "原团队": from_team,
                    "新团队": to_team,
                    "变更版本": new_version,
                    "变更方式": mode,
                    "操作人": operator,
                    "变更时间": now,
                    "备注": f"{self.team_name(from_team)}移交{self.team_name(to_team)}",
                })
            from_snapshot = self.build_snapshot(from_team, new_version)
            to_snapshot = self.build_snapshot(to_team, new_version)
        summary: dict[str, Any] = {
            "version": new_version,
            "mode": mode,
            "moved": [plan.get("计划编号", "") for plan in moving],
            "from_snapshot": from_snapshot.get("快照编号"),
            "to_snapshot": to_snapshot.get("快照编号"),
        }
        if mode == HANDOVER_FULL:
            message = (
                f"整体刷新完成：{len(moving)} 条计划按版本 {new_version} 整体移交"
                f"{self.team_name(to_team)}，双方团队计划快照已整体按新归属生效"
            )
        else:
            message = (
                f"逐条撤销完成：{len(moving)} 条计划按条目逐个切换归属至"
                f"{self.team_name(to_team)}，版本 {new_version}"
            )
        return True, message, summary

    # ---------- 归属变更与待审核工作台 ----------
    def ownership_changes(self, team_code: str | None = None) -> list[dict[str, Any]]:
        rows = store.rows(CHANGE_MODULE)
        if team_code:
            rows = [row for row in rows if row.get("原团队") == team_code or row.get("新团队") == team_code]
        return list(reversed(rows))

    def workbench_items(self, status: str | None = None) -> list[dict[str, Any]]:
        rows = store.rows(WORKBENCH_MODULE)
        if status:
            rows = [row for row in rows if row.get("状态") == status]
        return list(reversed(rows))
