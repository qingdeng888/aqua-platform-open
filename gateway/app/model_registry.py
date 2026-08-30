"""模型管理 —— 控制台「模型管理」页后端

对外模型列表 = 上游实时全量列表（不再有硬编码白名单）± 管理员覆盖层：
  - hidden=1：从 /v1/models 等对外列表中隐藏；
  - manual=1：上游 /models 尚未收录、由管理员手动补录的模型 ID。

「隐藏」默认只影响列表可见性，下游指名调用照旧放行；打开开关
hidden_models_block_calls 后，隐藏模型被调用时返回 400 model_disabled。
两种语义共存，切换只改一个设置项。

可见性与「名称是否有效」是两件事：模型 ID 纠错（request_validator）始终基于
上游真实存在的全量集合（含被隐藏项），否则隐藏一个模型会让指名调用它的请求被
模糊匹配改写到另一个相近模型上，属于静默换模型的严重行为。

覆盖表行数与模型数同阶（几十行）且全字段明文，故整表读入进程内快照缓存，
写操作显式失效；开关值随同一次快照读出，避免每次调用都查库。
本模块不依赖 public_api，避免 public_api ↔ admin_api ↔ 本模块的循环导入。

端点（均需管理员 Token）：
  GET    /gw/admin/models                  列表（上游实时 + 覆盖态 + 统计 + 开关态）
  POST   /gw/admin/models                  手动补录一个模型
  DELETE /gw/admin/models?model_id=...     删除手动补录项
  PUT    /gw/admin/models/visibility       批量隐藏/显示
  PUT    /gw/admin/models/block-setting    设置「隐藏的模型同时禁止调用」开关
"""
import asyncio
import logging
import re
import time
from typing import Optional

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict

from app.admin_api import require_admin
from app.database import (
    execute, fetch_all, get_setting, insert_audit, insert_audit_many, set_setting, utcnow,
)

logger = logging.getLogger("acu.model_registry")

router = APIRouter(prefix="/gw/admin", tags=["模型管理"])

BLOCK_SETTING_KEY = "hidden_models_block_calls"   # 「隐藏即禁用」开关的设置项键名
MANUAL_OWNER = "manual"                           # 手动补录模型在列表里的 owned_by
MODEL_ID_MAX_LEN = 200                            # 模型 ID 长度上限
MODEL_ID_RE = re.compile(r"^[A-Za-z0-9._:/-]+$")  # 形如 publisher/model-name-v1.5
VISIBILITY_MAX_IDS = 500                          # 单次批量改可见性的 ID 上限
REMARK_MAX_LEN = 200                              # 备注长度上限

_CACHE_TTL = 30.0                                 # 快照兜底 TTL（写操作会显式失效）
_cache: dict = {"overrides": None, "block": False, "expires": 0.0}


# ========== 纯函数（可单测，不碰库） ==========

def normalize_model_id(raw: Optional[str]) -> str:
    """校验并规范化模型 ID；非法时抛 ValueError（模型 ID 非机密，原因可回显）"""
    mid = (raw or "").strip()
    if not mid:
        raise ValueError("模型 ID 不能为空")
    if len(mid) > MODEL_ID_MAX_LEN:
        raise ValueError(f"模型 ID 过长，上限 {MODEL_ID_MAX_LEN} 字符")
    if not MODEL_ID_RE.match(mid):
        raise ValueError("模型 ID 仅允许字母、数字与 . _ : / - ，且不含空格")
    return mid


def apply_overrides(upstream: list, overrides: Optional[dict]) -> list:
    """上游实时列表 + 覆盖层 → 对外可见列表

    剔除 hidden，追加上游没有的 manual 项（按 ID 排序，保证输出稳定）。
    """
    ov = overrides or {}
    hidden = {mid for mid, it in ov.items() if it.get("hidden")}
    upstream_ids = set()
    visible = []
    for m in upstream or []:
        mid = m.get("id", "") if isinstance(m, dict) else ""
        if not mid:
            continue
        upstream_ids.add(mid)
        if mid not in hidden:
            visible.append(m)
    extra = sorted(
        mid for mid, it in ov.items()
        if it.get("manual") and mid not in upstream_ids and mid not in hidden
    )
    visible.extend({"id": mid, "object": "model", "owned_by": MANUAL_OWNER} for mid in extra)
    return visible


def all_known_models(upstream: list, overrides: Optional[dict]) -> list:
    """全部「真实存在」的模型（上游全量 ∪ 手动补录，含被隐藏项）——供模型 ID 纠错用"""
    ov = overrides or {}
    ids = []
    seen = set()
    for m in upstream or []:
        mid = m.get("id", "") if isinstance(m, dict) else ""
        if mid and mid not in seen:
            seen.add(mid)
            ids.append(mid)
    for mid in sorted(mid for mid, it in ov.items() if it.get("manual")):
        if mid not in seen:
            seen.add(mid)
            ids.append(mid)
    return [{"id": mid} for mid in ids]


def build_admin_rows(upstream: list, overrides: Optional[dict], search: str = "") -> list:
    """控制台表格行：上游全量 + 手动补录，各自带覆盖态；search 为子串过滤（不区分大小写）"""
    ov = overrides or {}
    kw = (search or "").strip().lower()
    rows = []
    seen = set()

    def emit(mid: str, source: str):
        it = ov.get(mid) or {}
        rows.append({
            "model_id": mid,
            "source": source,
            "hidden": bool(it.get("hidden")),
            "manual": bool(it.get("manual")),
            "remark": it.get("remark") or "",
            "updated_at": it.get("updated_at") or "",
        })

    for m in upstream or []:
        mid = m.get("id", "") if isinstance(m, dict) else ""
        if not mid or mid in seen:
            continue
        seen.add(mid)
        emit(mid, "upstream")
    for mid in sorted(mid for mid, it in ov.items() if it.get("manual")):
        if mid not in seen:
            seen.add(mid)
            emit(mid, "manual")

    if kw:
        rows = [r for r in rows if kw in r["model_id"].lower() or kw in r["remark"].lower()]
    return rows


# ========== 覆盖层快照 ==========

def invalidate() -> None:
    """覆盖层或开关变更后立即失效快照（单 worker，进程内失效即全局生效）"""
    _cache["overrides"] = None
    _cache["expires"] = 0.0


def _load_snapshot() -> tuple:
    """同步读取覆盖表与开关（在线程里跑）"""
    rows = fetch_all(
        "SELECT model_id, hidden, manual, remark, updated_at FROM model_overrides"
    )
    overrides = {
        r["model_id"]: {
            "hidden": bool(r.get("hidden")),
            "manual": bool(r.get("manual")),
            "remark": r.get("remark") or "",
            "updated_at": r.get("updated_at") or "",
        }
        for r in rows
    }
    block = (get_setting(BLOCK_SETTING_KEY) or "false").strip().lower() == "true"
    return overrides, block


async def get_snapshot() -> tuple:
    """(overrides, block_enabled)，30 秒快照 + 写失效"""
    if _cache["overrides"] is not None and _cache["expires"] > time.time():
        return _cache["overrides"], _cache["block"]
    try:
        overrides, block = await asyncio.to_thread(_load_snapshot)
    except Exception as e:
        # 读不到覆盖层时按「无覆盖」放行：宁可多列出模型，也不要因为一次库抖动就清空模型列表
        logger.error(f"读取模型覆盖层失败，本次按无覆盖处理: {e}")
        return _cache["overrides"] or {}, _cache["block"]
    _cache["overrides"] = overrides
    _cache["block"] = block
    _cache["expires"] = time.time() + _CACHE_TTL
    return overrides, block


async def get_overrides() -> dict:
    overrides, _ = await get_snapshot()
    return overrides


async def is_call_blocked(model_id: str) -> bool:
    """该模型是否应拒绝调用（被隐藏 且 开关打开）"""
    if not model_id:
        return False
    overrides, block = await get_snapshot()
    if not block:
        return False
    return bool((overrides.get(model_id) or {}).get("hidden"))


# ========== 请求模型 ==========

class ManualModelRequest(BaseModel):
    model_config = ConfigDict(protected_namespaces=())
    model_id: str
    remark: str = ""


class VisibilityRequest(BaseModel):
    model_config = ConfigDict(protected_namespaces=())
    model_ids: list
    hidden: bool


class BlockSettingRequest(BaseModel):
    model_config = ConfigDict(protected_namespaces=())
    block_calls: bool


# ========== 端点 ==========

@router.get("/models", tags=["管理员"])
async def list_managed_models(
    request: Request,
    search: str = Query("", max_length=200),
    refresh: int = Query(0, ge=0, le=1),
):
    """模型管理列表：上游实时全量 + 覆盖态。refresh=1 强制回源上游"""
    await require_admin(request)
    from app.public_api import _models_cache, fetch_upstream_models

    if refresh:
        _models_cache["expires"] = 0     # 与 /gw/admin/sync-models 同一手法
    upstream = await fetch_upstream_models()
    overrides, block = await get_snapshot()
    rows = build_admin_rows(upstream, overrides, search)

    total_upstream = sum(1 for m in upstream if isinstance(m, dict) and m.get("id"))
    manual_count = sum(1 for it in overrides.values() if it.get("manual"))
    hidden_count = sum(1 for it in overrides.values() if it.get("hidden"))
    return {
        "models": rows,
        "matched": len(rows),
        "upstream_count": total_upstream,
        "manual_count": manual_count,
        "hidden_count": hidden_count,
        "visible_count": len(apply_overrides(upstream, overrides)),
        "block_calls": block,
        "search": search,
    }


@router.post("/models", tags=["管理员"])
async def add_manual_model(request: Request, req: ManualModelRequest):
    """手动补录模型：上游 /models 未收录但确实可调用时用；同名已隐藏项会一并取消隐藏"""
    await require_admin(request)
    try:
        mid = normalize_model_id(req.model_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail={
            "message": str(e), "type": "invalid_request_error", "code": "invalid_model_id",
        })
    remark = (req.remark or "").strip()[:REMARK_MAX_LEN]
    now = utcnow()

    def _write():
        execute(
            "INSERT INTO model_overrides (model_id, hidden, manual, remark, created_at, updated_at) "
            "VALUES (%s, 0, 1, %s, %s, %s) "
            "ON CONFLICT (model_id) DO UPDATE SET manual = 1, hidden = 0, "
            "remark = EXCLUDED.remark, updated_at = EXCLUDED.updated_at",
            (mid, remark, now, now),
        )
        insert_audit("add_manual_model", "model", mid, f"手动补录模型 {mid}")

    await asyncio.to_thread(_write)
    invalidate()
    logger.info(f"手动补录模型: {mid}")
    return {"message": f"已添加模型 {mid}", "model_id": mid}


@router.delete("/models", tags=["管理员"])
async def delete_manual_model(request: Request, model_id: str = Query(..., max_length=300)):
    """删除手动补录项；上游自带的模型不能删除（要不显示请用隐藏）"""
    await require_admin(request)
    mid = (model_id or "").strip()
    if not mid:
        raise HTTPException(status_code=400, detail={
            "message": "缺少 model_id", "type": "invalid_request_error", "code": "invalid_model_id",
        })

    def _write():
        n = execute("DELETE FROM model_overrides WHERE model_id = %s AND manual = 1", (mid,))
        if n:
            insert_audit("delete_manual_model", "model", mid, f"删除手动补录模型 {mid}")
        return n

    removed = await asyncio.to_thread(_write)
    if not removed:
        raise HTTPException(status_code=404, detail={
            "message": "该模型不是手动补录项，无法删除；如需对外不可见请使用「隐藏」",
            "type": "invalid_request_error",
            "code": "not_manual_model",
        })
    invalidate()
    logger.info(f"删除手动补录模型: {mid}")
    return {"message": f"已删除模型 {mid}", "model_id": mid}


@router.put("/models/visibility", tags=["管理员"])
async def set_models_visibility(request: Request, req: VisibilityRequest):
    """批量隐藏/显示。显示时：手动项置 hidden=0，上游项直接删行（无信息的行不留）"""
    await require_admin(request)
    ids = []
    seen = set()
    for raw in (req.model_ids or []):
        try:
            mid = normalize_model_id(raw if isinstance(raw, str) else str(raw))
        except ValueError as e:
            raise HTTPException(status_code=400, detail={
                "message": str(e), "type": "invalid_request_error", "code": "invalid_model_id",
            })
        if mid not in seen:
            seen.add(mid)
            ids.append(mid)
    if not ids:
        raise HTTPException(status_code=400, detail={
            "message": "model_ids 不能为空", "type": "invalid_request_error", "code": "empty_model_ids",
        })
    if len(ids) > VISIBILITY_MAX_IDS:
        raise HTTPException(status_code=400, detail={
            "message": f"单次最多处理 {VISIBILITY_MAX_IDS} 个模型，请分批操作",
            "type": "invalid_request_error",
            "code": "too_many_model_ids",
        })

    hidden = bool(req.hidden)
    now = utcnow()
    action = "hide_model" if hidden else "show_model"

    def _write():
        if hidden:
            placeholders = ", ".join(["(%s, 1, 0, '', %s, %s)"] * len(ids))
            params = []
            for mid in ids:
                params.extend([mid, now, now])
            execute(
                "INSERT INTO model_overrides (model_id, hidden, manual, remark, created_at, updated_at) "
                "VALUES " + placeholders +
                " ON CONFLICT (model_id) DO UPDATE SET hidden = 1, updated_at = EXCLUDED.updated_at",
                tuple(params),
            )
        else:
            execute(
                "UPDATE model_overrides SET hidden = 0, updated_at = %s "
                "WHERE model_id = ANY(%s) AND manual = 1",
                (now, ids),
            )
            execute("DELETE FROM model_overrides WHERE model_id = ANY(%s) AND manual = 0", (ids,))
        insert_audit_many([
            (action, "model", mid, ("隐藏模型 " if hidden else "取消隐藏模型 ") + mid) for mid in ids
        ])

    await asyncio.to_thread(_write)
    invalidate()
    logger.info(f"模型可见性变更: {'隐藏' if hidden else '显示'} {len(ids)} 个")
    return {
        "message": ("已隐藏 " if hidden else "已显示 ") + f"{len(ids)} 个模型",
        "hidden": hidden,
        "count": len(ids),
    }


@router.put("/models/block-setting", tags=["管理员"])
async def set_block_setting(request: Request, req: BlockSettingRequest):
    """设置「隐藏的模型同时禁止调用」：关=仅列表不显示；开=调用返回 400 model_disabled"""
    await require_admin(request)
    val = "true" if req.block_calls else "false"

    def _write():
        set_setting(BLOCK_SETTING_KEY, val)
        insert_audit("update_setting", "setting", BLOCK_SETTING_KEY,
                     f"隐藏的模型同时禁止调用 = {val}")

    await asyncio.to_thread(_write)
    invalidate()
    logger.info(f"隐藏模型调用拦截开关: {val}")
    return {
        "message": "已开启：隐藏的模型将拒绝调用" if req.block_calls else "已关闭：隐藏的模型仅不在列表显示",
        "block_calls": req.block_calls,
    }
