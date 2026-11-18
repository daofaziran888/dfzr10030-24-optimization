"""采集调度接口：维护调度计划，覆盖确认排期、启动调度、暂停调度等动作。

列表与详情按团队计划数据快照受控读取；移交、快照回查与归属变更
在同一批接口里暴露，快照复核结果由采集日志服务写回。
"""
from __future__ import annotations

import csv
import io
from dataclasses import replace

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from app.schemas import ActionPayload, ActionResult, HandoverPayload, SnapshotPageResult
from app.security import get_context
from app.services.schedule import ScheduleService

router = APIRouter(prefix="/api/schedule", tags=["采集调度"])

service = ScheduleService()

LIST_FIELDS = ["计划编号", "关联任务", "调度周期", "开始时间", "结束时间", "依赖任务", "失败策略", "计划状态"]
STATUSES = ["草稿", "已排期", "运行中", "已完成", "已暂停"]


def query_filters(request: Request) -> dict[str, str]:
    """只接受当前模块声明过的字段，忽略分页等保留参数。"""
    return {
        key: value.strip()
        for key, value in request.query_params.items()
        if key in LIST_FIELDS and value.strip()
    }


@router.get("/teams")
def list_teams() -> dict:
    """团队排期数据：快照按这里的团队范围汇总。"""
    return {"items": service.snapshots.teams()}


@router.get("/snapshot")
def get_snapshot(
    request: Request,
    team: str | None = Query(default=None, description="团队编号，默认取当前值班团队"),
    version: int | None = Query(default=None, description="快照版本；缺省为当前版本，指定则回查旧结果"),
) -> dict:
    """团队计划数据快照：当前版本或按版本回查旧结果。"""
    ctx = get_context(request)
    team_code = (team or ctx.team_code).strip()
    if version is None:
        snapshot = service.snapshots.ensure_snapshot(team_code)
    else:
        snapshot = service.snapshots.snapshot_for_version(team_code, version)
        if snapshot is None:
            raise HTTPException(status_code=404, detail=f"团队 {team_code} 不存在版本 {version} 的快照")
    return {"snapshot": snapshot, "历史版本": service.snapshots.snapshot_versions(team_code)}


@router.get("/ownership_changes")
def list_ownership_changes(
    request: Request,
    team: str | None = Query(default=None, description="团队编号，默认取当前值班团队"),
) -> dict:
    """归属变更记录：调度页按团队查看计划归属如何切换。"""
    ctx = get_context(request)
    return {"items": service.snapshots.ownership_changes((team or ctx.team_code).strip())}


@router.post("/handover", response_model=ActionResult)
def handover(payload: HandoverPayload, request: Request) -> ActionResult:
    """计划移交：整体刷新或逐条撤销，按版本边界一次生效。"""
    ok, message, summary = service.handover(payload, get_context(request))
    return ActionResult(ok=ok, message=message, entry=summary or None)


@router.get("", response_model=SnapshotPageResult)
def list_entries(
    request: Request,
    keyword: str | None = Query(default=None, description="按计划编号检索"),
    status: str | None = Query(default=None, description="草稿、已排期、运行中、已完成、已暂停"),
    team: str | None = Query(default=None, description="团队编号，默认取当前值班团队"),
    shared: bool = Query(default=False, description="是否并入共享计划列表（他团队计划打码展示）"),
    page: int = Query(default=1, ge=1),
    size: int = Query(default=20, ge=1, le=200),
) -> SnapshotPageResult:
    """按团队计划快照读取调度列表：团队只看到本归属计划，结果在排期范围内稳定。"""
    ctx = get_context(request)
    if team and team.strip():
        ctx = replace(ctx, team_code=team.strip())
    items, total, snapshot = service.list_entries(
        ctx=ctx,
        filters=query_filters(request),
        keyword=keyword,
        status=status,
        shared=shared,
        page=page,
        size=size,
    )
    return SnapshotPageResult(items=items, total=total, page=page, size=size, snapshot=snapshot)


@router.get("/export")
def export_entries(
    request: Request,
    keyword: str | None = Query(default=None),
    status: str | None = Query(default=None),
) -> StreamingResponse:
    """下载当前团队快照内的 UTF-8 CSV；必须声明在 /{entry_id} 之前。"""
    items, _, _ = service.list_entries(
        ctx=get_context(request),
        filters=query_filters(request),
        keyword=keyword,
        status=status,
        page=1,
        size=1000000,
    )
    output = io.StringIO()
    output.write("﻿")
    writer = csv.writer(output, lineterminator="\n")
    writer.writerow(["id", *LIST_FIELDS, "status", "归属团队", "归属版本"])
    for item in items:
        row = []
        for value in [
            item.get("id"),
            *(item.get(field, "") for field in LIST_FIELDS),
            item.get("status", ""),
            item.get("归属团队", ""),
            item.get("归属版本", ""),
        ]:
            text = "" if value is None else str(value)
            row.append("'" + text if text.startswith(("=", "+", "-", "@")) else text)
        writer.writerow(row)
    return StreamingResponse(
        iter([output.getvalue()]), media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="schedule.csv"'})


@router.get("/{entry_id}", response_model=dict)
def get_entry(entry_id: int, request: Request) -> dict:
    """读取单条调度计划明细；仅当前归属团队可读，越权读取会被拦截并留痕。"""
    entry, message, forbidden = service.get_entry(entry_id, get_context(request))
    if entry is None:
        raise HTTPException(status_code=403 if forbidden else 404, detail=message)
    return entry


@router.post("", response_model=ActionResult)
def create_entry(payload: dict[str, object], request: Request) -> ActionResult:
    """登记一条调度计划：计划绑定当前团队与状态，并同步写入团队计划快照。"""
    entry, message = service.create_entry(payload, get_context(request))
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message="调度计划已登记，并写入本团队计划快照", entry=entry)


@router.post("/{entry_id}/actions", response_model=ActionResult)
def run_action(entry_id: int, payload: ActionPayload, request: Request) -> ActionResult:
    """对单条调度计划执行确认排期、启动调度、暂停调度；不在快照内的计划操作会被拒绝。"""
    entry, message = service.run_action(entry_id, payload.action.strip(), get_context(request))
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)
