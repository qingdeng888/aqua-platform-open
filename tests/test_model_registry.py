"""模型管理单元测试（gateway/app/model_registry.py 的纯函数与契约，不连库不联网）

白名单已下线：对外模型列表 = 上游 /models 实时全量 ± 管理员覆盖层
（hidden 隐藏、manual 手动补录），是否连调用一起禁掉由开关
hidden_models_block_calls 决定（默认关：仅列表不显示）。

覆盖：
- normalize_model_id：空/超长/非法字符/两侧空白
- apply_overrides：剔除隐藏、追加手动补录、去重、脏数据跳过、输出稳定
- all_known_models：含被隐藏项（可见性 ≠ 名称有效性，隐藏不得触发模糊改写）
- build_admin_rows：来源/状态标记、按 ID 与备注搜索、手动项排在上游项之后
- is_call_blocked：开关关 → 一律放行；开关开 → 仅拦被隐藏的模型
- 契约守卫：端点注册与鉴权、删除仅限手动项、取消隐藏不留无信息行、
  写操作走线程 + 失效缓存 + 落审计、建表与默认设置、路由注册、白名单确已移除
"""
import os
from pathlib import Path

import pytest

# 导入链上两处模块级强校验：database 要 PG_PASSWORD、admin_api 要管理员密码。
# 单测不连库不登录，仅提供占位值满足导入。
os.environ.setdefault("PG_PASSWORD", "unit-test-no-connection")
os.environ.setdefault("ACU_ADMIN_PASSWORD", "unit-test-placeholder")

from app.model_registry import (  # noqa: E402
    BLOCK_SETTING_KEY,
    MANUAL_OWNER,
    MODEL_ID_MAX_LEN,
    VISIBILITY_MAX_IDS,
    _cache,
    all_known_models,
    apply_overrides,
    build_admin_rows,
    is_call_blocked,
    normalize_model_id,
)

_REPO = Path(__file__).resolve().parent.parent
_SRC = (_REPO / "gateway" / "app" / "model_registry.py").read_text(encoding="utf-8")
_PUBLIC_SRC = (_REPO / "gateway" / "app" / "public_api.py").read_text(encoding="utf-8")
_DB_SRC = (_REPO / "gateway" / "app" / "database.py").read_text(encoding="utf-8")
_MAIN_SRC = (_REPO / "gateway" / "app" / "main.py").read_text(encoding="utf-8")

_UP = [{"id": "a/one"}, {"id": "b/two"}, {"id": "c/three"}]


def _ov(**kw):
    """构造一条覆盖项，字段缺省与 _load_snapshot 输出保持一致"""
    it = {"hidden": False, "manual": False, "remark": "", "updated_at": ""}
    it.update(kw)
    return it


class TestNormalizeModelId:
    def test_strips_whitespace(self):
        assert normalize_model_id("  meta/llama-3.1-8b  ") == "meta/llama-3.1-8b"

    @pytest.mark.parametrize("raw", ["", "   ", None, "\t\n"])
    def test_empty_rejected(self, raw):
        with pytest.raises(ValueError, match="不能为空"):
            normalize_model_id(raw)

    def test_too_long_rejected(self):
        with pytest.raises(ValueError, match="过长"):
            normalize_model_id("a" * (MODEL_ID_MAX_LEN + 1))

    def test_max_length_accepted(self):
        assert normalize_model_id("a" * MODEL_ID_MAX_LEN)

    @pytest.mark.parametrize("bad", [
        "has space/model", "模型/中文", "a/b;c", "a/b'c", "<script>", "a/b\"c", "a\nb",
    ])
    def test_illegal_chars_rejected(self, bad):
        with pytest.raises(ValueError, match="仅允许"):
            normalize_model_id(bad)

    @pytest.mark.parametrize("ok", [
        "nvidia/llama-3.3-nemotron-super-49b-v1.5", "deepseek-ai/deepseek-v4-flash",
        "baidu/paddleocr", "zh_ns/model_1", "vendor/model:latest", "plain-model",
    ])
    def test_real_world_ids_accepted(self, ok):
        assert normalize_model_id(ok) == ok


class TestApplyOverrides:
    def test_no_overrides_is_passthrough(self):
        for ov in (None, {}):
            assert apply_overrides(_UP, ov) == _UP

    def test_hidden_removed_order_preserved(self):
        out = apply_overrides(_UP, {"b/two": _ov(hidden=True)})
        assert [m["id"] for m in out] == ["a/one", "c/three"]

    def test_manual_appended_sorted_after_upstream(self):
        ov = {"z/manual": _ov(manual=True), "d/manual": _ov(manual=True)}
        out = apply_overrides(_UP, ov)
        assert [m["id"] for m in out] == ["a/one", "b/two", "c/three", "d/manual", "z/manual"]

    def test_manual_entry_shape(self):
        (extra,) = [m for m in apply_overrides([], {"x/y": _ov(manual=True)})]
        assert extra == {"id": "x/y", "object": "model", "owned_by": MANUAL_OWNER}

    def test_manual_and_hidden_not_listed(self):
        assert apply_overrides([], {"x/y": _ov(manual=True, hidden=True)}) == []

    def test_manual_duplicate_of_upstream_not_appended(self):
        # 上游后来收录了同一个 ID：以上游条目为准（保留其 created/owned_by 等字段）
        out = apply_overrides(_UP, {"b/two": _ov(manual=True)})
        assert [m["id"] for m in out] == ["a/one", "b/two", "c/three"]
        assert out[1] is _UP[1]

    def test_hidden_id_absent_from_upstream_is_noop(self):
        assert apply_overrides(_UP, {"nope/x": _ov(hidden=True)}) == _UP

    def test_dirty_upstream_entries_skipped(self):
        dirty = [{"id": "a/one"}, {"id": ""}, {"no_id": 1}, "string-entry", None]
        assert [m["id"] for m in apply_overrides(dirty, {})] == ["a/one"]

    def test_empty_upstream_with_manual_only(self):
        for up in (None, []):
            assert [m["id"] for m in apply_overrides(up, {"x/y": _ov(manual=True)})] == ["x/y"]


class TestAllKnownModels:
    def test_hidden_stays_known(self):
        # 关键：隐藏只影响列表可见性；名称仍然有效，否则指名调用会被模糊匹配改写到别的模型
        ids = [m["id"] for m in all_known_models(_UP, {"b/two": _ov(hidden=True)})]
        assert ids == ["a/one", "b/two", "c/three"]

    def test_manual_included_even_when_hidden(self):
        ov = {"x/y": _ov(manual=True, hidden=True)}
        assert [m["id"] for m in all_known_models(_UP, ov)][-1] == "x/y"

    def test_upstream_order_then_sorted_manual(self):
        ov = {"z/m": _ov(manual=True), "d/m": _ov(manual=True)}
        assert [m["id"] for m in all_known_models(_UP, ov)] == [
            "a/one", "b/two", "c/three", "d/m", "z/m"]

    def test_dedupes_upstream_repeats(self):
        assert [m["id"] for m in all_known_models([{"id": "a"}, {"id": "a"}], {})] == ["a"]

    def test_shape_is_id_only_dicts(self):
        assert all_known_models([{"id": "a", "extra": 1}], {}) == [{"id": "a"}]


class TestBuildAdminRows:
    def test_flags_and_source(self):
        ov = {"b/two": _ov(hidden=True, remark="太贵"), "x/y": _ov(manual=True)}
        rows = {r["model_id"]: r for r in build_admin_rows(_UP, ov)}
        assert rows["a/one"]["source"] == "upstream" and not rows["a/one"]["hidden"]
        assert rows["b/two"]["hidden"] and rows["b/two"]["remark"] == "太贵"
        assert rows["x/y"]["source"] == "manual" and rows["x/y"]["manual"]

    def test_manual_rows_after_upstream_rows(self):
        ov = {"a/manual": _ov(manual=True)}
        assert [r["model_id"] for r in build_admin_rows(_UP, ov)][-1] == "a/manual"

    def test_manual_present_upstream_not_duplicated(self):
        ov = {"b/two": _ov(manual=True)}
        ids = [r["model_id"] for r in build_admin_rows(_UP, ov)]
        assert ids.count("b/two") == 1

    @pytest.mark.parametrize("kw,expect", [
        ("two", ["b/two"]),
        ("TWO", ["b/two"]),
        ("  two  ", ["b/two"]),
        ("/", ["a/one", "b/two", "c/three"]),
        ("nomatch", []),
        ("", ["a/one", "b/two", "c/three"]),
    ])
    def test_search_on_model_id(self, kw, expect):
        assert [r["model_id"] for r in build_admin_rows(_UP, {}, kw)] == expect

    def test_search_matches_remark(self):
        ov = {"c/three": _ov(hidden=True, remark="上游已弃用")}
        assert [r["model_id"] for r in build_admin_rows(_UP, ov, "弃用")] == ["c/three"]


class TestIsCallBlocked:
    """开关语义：关=仅列表不显示（放行调用）；开=隐藏的模型返回 400"""

    def _seed(self, overrides, block):
        import time
        _cache["overrides"] = overrides
        _cache["block"] = block
        _cache["expires"] = time.time() + 300     # 快照未过期，不会触发查库

    def teardown_method(self):
        _cache["overrides"] = None
        _cache["block"] = False
        _cache["expires"] = 0.0

    @pytest.mark.asyncio
    async def test_switch_off_allows_hidden_model(self):
        self._seed({"b/two": _ov(hidden=True)}, False)
        assert await is_call_blocked("b/two") is False

    @pytest.mark.asyncio
    async def test_switch_on_blocks_hidden_model(self):
        self._seed({"b/two": _ov(hidden=True)}, True)
        assert await is_call_blocked("b/two") is True

    @pytest.mark.asyncio
    async def test_switch_on_allows_visible_model(self):
        self._seed({"b/two": _ov(hidden=True)}, True)
        assert await is_call_blocked("a/one") is False

    @pytest.mark.asyncio
    async def test_manual_visible_model_never_blocked(self):
        self._seed({"x/y": _ov(manual=True)}, True)
        assert await is_call_blocked("x/y") is False

    @pytest.mark.asyncio
    async def test_empty_model_id_not_blocked(self):
        self._seed({"b/two": _ov(hidden=True)}, True)
        assert await is_call_blocked("") is False


class TestContract:
    """源码契约守卫"""

    def _body(self, fn: str) -> str:
        return _SRC.split(f"async def {fn}(")[1].split("\n\n\n")[0]

    @pytest.mark.parametrize("decorator,fn", [
        ('@router.get("/models", tags=["管理员"])', "list_managed_models"),
        ('@router.post("/models", tags=["管理员"])', "add_manual_model"),
        ('@router.delete("/models", tags=["管理员"])', "delete_manual_model"),
        ('@router.put("/models/visibility", tags=["管理员"])', "set_models_visibility"),
        ('@router.put("/models/block-setting", tags=["管理员"])', "set_block_setting"),
    ])
    def test_endpoints_registered_and_require_admin(self, decorator, fn):
        assert decorator in _SRC
        assert "await require_admin(request)" in self._body(fn)

    def test_router_prefix_is_admin(self):
        assert 'APIRouter(prefix="/gw/admin"' in _SRC

    def test_router_registered_in_main(self):
        assert "from app.model_registry import router as model_registry_router" in _MAIN_SRC
        assert "app.include_router(model_registry_router)" in _MAIN_SRC

    def test_delete_only_removes_manual_rows(self):
        # 上游自带模型不允许删除（要不可见请用隐藏），否则下次回源又冒出来
        body = self._body("delete_manual_model")
        assert "DELETE FROM model_overrides WHERE model_id = %s AND manual = 1" in body
        assert '"not_manual_model"' in body

    def test_unhide_leaves_no_informationless_row(self):
        # 取消隐藏：手动项置 hidden=0，上游项直接删行——表里只留携带信息的行
        body = self._body("set_models_visibility")
        assert "SET hidden = 0" in body and "AND manual = 1" in body
        assert "DELETE FROM model_overrides WHERE model_id = ANY(%s) AND manual = 0" in body

    def test_visibility_batch_capped(self):
        assert VISIBILITY_MAX_IDS > 0
        assert "VISIBILITY_MAX_IDS" in self._body("set_models_visibility")

    @pytest.mark.parametrize("fn", [
        "add_manual_model", "delete_manual_model", "set_models_visibility", "set_block_setting",
    ])
    def test_writes_run_in_thread_and_invalidate_cache(self, fn):
        body = self._body(fn)
        assert "asyncio.to_thread(_write)" in body
        assert "invalidate()" in body

    @pytest.mark.parametrize("fn,audit", [
        ("add_manual_model", "insert_audit("),
        ("delete_manual_model", "insert_audit("),
        ("set_models_visibility", "insert_audit_many("),
        ("set_block_setting", "insert_audit("),
    ])
    def test_writes_are_audited(self, fn, audit):
        assert audit in self._body(fn)

    def test_snapshot_read_needs_no_decryption(self):
        # 覆盖表全字段明文，读取不该出现解密（与上游密钥查重形成对照）
        assert "decrypt" not in _SRC

    def test_setting_key_seeded_with_default_off(self):
        assert BLOCK_SETTING_KEY == "hidden_models_block_calls"
        assert f'"{BLOCK_SETTING_KEY}": "false"' in _DB_SRC

    def test_table_created_idempotently(self):
        assert "CREATE TABLE IF NOT EXISTS model_overrides" in _DB_SRC
        assert "idx_model_overrides_hidden" in _DB_SRC


class TestPublicApiContract:
    """白名单下线后的取数链契约"""

    def test_whitelist_removed(self):
        assert "_VERIFIED_WORKING_MODELS" not in _PUBLIC_SRC
        assert "filtered_models" not in _PUBLIC_SRC

    def test_fetch_upstream_returns_full_list(self):
        body = _PUBLIC_SRC.split("async def fetch_upstream_models(")[1].split("\n\n\n")[0]
        assert 'models = [m for m in all_models if isinstance(m, dict) and m.get("id")]' in body
        assert "refresh_verified_models" not in body      # 纠错集合改由 get_model_list 推送

    def test_get_model_list_applies_overrides(self):
        body = _PUBLIC_SRC.split("async def get_model_list(")[1].split("\n\n\n")[0]
        assert "await fetch_upstream_models()" in body
        assert "apply_overrides(raw, overrides)" in body
        assert "refresh_verified_models(all_known_models(raw, overrides))" in body

    def test_public_endpoints_use_override_layer(self):
        # /v1/models 与 /api/v1/models 共用 list_models（双装饰器），/api/public/models 独立一份，
        # 三个对外路径都必须取覆盖层结果，不能直接拿上游全量
        assert '@router.get("/v1/models"' in _PUBLIC_SRC
        assert '@router.get("/api/v1/models"' in _PUBLIC_SRC
        for fn in ("list_models", "public_models"):
            body = _PUBLIC_SRC.split(f"async def {fn}(")[1].split("\n\n\n")[0]
            assert "models = await get_model_list()" in body
            assert "fetch_upstream_models()" not in body

    def test_chat_path_blocks_disabled_model(self):
        assert "if await is_call_blocked(model):" in _PUBLIC_SRC
        assert '"code": "model_disabled",' in _PUBLIC_SRC

    def test_unknown_model_still_forwarded(self):
        # 上游随时可能上新模型：名字不在列表里只记日志，不拦截
        assert "不拦截，仅记录日志" in _PUBLIC_SRC
