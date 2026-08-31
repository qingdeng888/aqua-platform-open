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
- resolve_alias_pure / apply_aliases / build_alias_rows：别名层（改名）纯函数
- 契约守卫：端点注册与鉴权、删除仅限手动项、取消隐藏不留无信息行、
  写操作走线程 + 失效缓存 + 落审计、建表与默认设置、路由注册、白名单确已移除、
  别名解析发生在纠错之前、别名不进纠错集合
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
    apply_aliases,
    apply_overrides,
    build_admin_rows,
    build_alias_rows,
    is_call_blocked,
    normalize_model_id,
    resolve_alias_pure,
)

_REPO = Path(__file__).resolve().parent.parent
_SRC = (_REPO / "gateway" / "app" / "model_registry.py").read_text(encoding="utf-8")
_PUBLIC_SRC = (_REPO / "gateway" / "app" / "public_api.py").read_text(encoding="utf-8")
_DB_SRC = (_REPO / "gateway" / "app" / "database.py").read_text(encoding="utf-8")
_MAIN_SRC = (_REPO / "gateway" / "app" / "main.py").read_text(encoding="utf-8")
_TEST_SRC = (_REPO / "gateway" / "app" / "model_test.py").read_text(encoding="utf-8")

_UP = [{"id": "a/one"}, {"id": "b/two"}, {"id": "c/three"}]


def _ov(**kw):
    """构造一条覆盖项，字段缺省与 _load_snapshot 输出保持一致"""
    it = {"hidden": False, "manual": False, "remark": "", "updated_at": ""}
    it.update(kw)
    return it


def _al(target, **kw):
    """构造一条别名项，字段缺省与 _load_snapshot 输出保持一致（force_mapping 默认开）"""
    it = {
        "target_model": target, "display_name": "", "keep_original": False,
        "force_mapping": True, "remark": "", "updated_at": "",
    }
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

    def test_rows_carry_aliases(self):
        al = {"nv/x": _al("b/two"), "nv/y": _al("b/two"), "nv/z": _al("a/one")}
        rows = {r["model_id"]: r for r in build_admin_rows(_UP, {}, "", al)}
        assert rows["b/two"]["aliases"] == ["nv/x", "nv/y"]     # 按别名排序，输出稳定
        assert rows["a/one"]["aliases"] == ["nv/z"]
        assert rows["c/three"]["aliases"] == []

    def test_search_matches_alias_text(self):
        al = {"nv/kimi": _al("c/three")}
        assert [r["model_id"] for r in build_admin_rows(_UP, {}, "kimi", al)] == ["c/three"]


class TestResolveAlias:
    """别名 → 真名：三级确定性匹配，未命中原样返回"""

    _AL = {"nv/kimi-k3": _al("moonshotai/kimi-k3", force_mapping=True)}

    def test_exact_hit(self):
        assert resolve_alias_pure("nv/kimi-k3", self._AL) == (
            "moonshotai/kimi-k3", "nv/kimi-k3", True)

    @pytest.mark.parametrize("probe", ["NV/Kimi-K3", "nv/KIMI-K3"])
    def test_case_insensitive_hit(self, probe):
        real, used, _ = resolve_alias_pure(probe, self._AL)
        assert (real, used) == ("moonshotai/kimi-k3", "nv/kimi-k3")

    @pytest.mark.parametrize("probe", ["nv_kimi_k3", "NVKimiK3", "nv.kimi.k3"])
    def test_normalized_hit(self, probe):
        # 与 request_validator._normalize 同规则：去掉所有非字母数字后比对
        real, used, _ = resolve_alias_pure(probe, self._AL)
        assert (real, used) == ("moonshotai/kimi-k3", "nv/kimi-k3")

    def test_whitespace_trimmed(self):
        assert resolve_alias_pure("  nv/kimi-k3 ", self._AL)[0] == "moonshotai/kimi-k3"

    @pytest.mark.parametrize("probe", ["moonshotai/kimi-k3", "other/model", "nv/kimi-k9"])
    def test_miss_returns_input_unchanged(self, probe):
        assert resolve_alias_pure(probe, self._AL) == (probe, "", False)

    @pytest.mark.parametrize("aliases", [None, {}])
    def test_no_aliases_is_passthrough(self, aliases):
        assert resolve_alias_pure("a/one", aliases) == ("a/one", "", False)

    @pytest.mark.parametrize("probe", ["", "   ", None])
    def test_empty_input(self, probe):
        real, used, force = resolve_alias_pure(probe, self._AL)
        assert (used, force) == ("", False) and not real

    def test_force_mapping_passthrough(self):
        al = {"nv/x": _al("a/one", force_mapping=False)}
        assert resolve_alias_pure("nv/x", al) == ("a/one", "nv/x", False)

    def test_alias_without_target_ignored(self):
        # 脏数据（target 为空）不该把请求打到空模型名上
        assert resolve_alias_pure("nv/x", {"nv/x": _al("")}) == ("nv/x", "", False)

    def test_multiple_aliases_same_target(self):
        al = {"nv/a": _al("a/one"), "nv/b": _al("a/one")}
        assert resolve_alias_pure("nv/b", al)[:2] == ("a/one", "nv/b")


class TestApplyAliases:
    """可见列表 → 对外列表：默认替换真名，keep_original 则并存"""

    def test_no_aliases_is_passthrough(self):
        for al in (None, {}):
            assert apply_aliases(_UP, al) == _UP

    def test_alias_replaces_real_name(self):
        out = apply_aliases(_UP, {"nv/x": _al("b/two")})
        assert [m["id"] for m in out] == ["a/one", "nv/x", "c/three"]

    def test_keep_original_lists_both(self):
        out = apply_aliases(_UP, {"nv/x": _al("b/two", keep_original=True)})
        assert [m["id"] for m in out] == ["a/one", "b/two", "nv/x", "c/three"]

    def test_keep_original_is_or_across_aliases(self):
        # 同一 target 多条别名：任一条要求保留就保留（保留是更安全的一侧）
        al = {"nv/x": _al("b/two"), "nv/y": _al("b/two", keep_original=True)}
        assert [m["id"] for m in apply_aliases(_UP, al)] == [
            "a/one", "b/two", "nv/x", "nv/y", "c/three"]

    def test_multiple_aliases_sorted_and_in_place(self):
        al = {"nv/z": _al("b/two"), "nv/a": _al("b/two")}
        assert [m["id"] for m in apply_aliases(_UP, al)] == ["a/one", "nv/a", "nv/z", "c/three"]

    def test_owned_by_takes_provider_segment(self):
        out = apply_aliases([{"id": "b/two", "owned_by": "moonshotai"}], {"nv/x": _al("b/two")})
        assert out[0]["owned_by"] == "nv"

    def test_owned_by_kept_when_alias_has_no_slash(self):
        out = apply_aliases([{"id": "b/two", "owned_by": "moonshotai"}], {"kimi": _al("b/two")})
        assert out[0]["owned_by"] == "moonshotai"

    def test_other_fields_inherited(self):
        src = [{"id": "b/two", "object": "model", "created": 123, "context_length": 8192}]
        out = apply_aliases(src, {"nv/x": _al("b/two")})
        assert out[0]["created"] == 123 and out[0]["context_length"] == 8192

    def test_display_name_applied(self):
        out = apply_aliases([{"id": "b/two"}], {"nv/x": _al("b/two", display_name="Kimi K3")})
        assert out[0]["display_name"] == "Kimi K3"

    def test_original_entry_not_mutated(self):
        src = [{"id": "b/two", "owned_by": "moonshotai"}]
        apply_aliases(src, {"nv/x": _al("b/two", display_name="X")})
        assert src[0] == {"id": "b/two", "owned_by": "moonshotai"}

    def test_hidden_target_suppresses_alias_entry(self):
        # 隐藏语义优先：target 已被覆盖层剔除，其别名条目也不该出现
        visible = apply_overrides(_UP, {"b/two": _ov(hidden=True)})
        out = apply_aliases(visible, {"nv/x": _al("b/two")})
        assert [m["id"] for m in out] == ["a/one", "c/three"]

    def test_alias_to_unknown_target_not_listed(self):
        assert [m["id"] for m in apply_aliases(_UP, {"nv/x": _al("nope/x")})] == [
            "a/one", "b/two", "c/three"]

    def test_dirty_entries_skipped(self):
        dirty = [{"id": "a/one"}, {"id": ""}, {"no_id": 1}, "s", None]
        assert [m["id"] for m in apply_aliases(dirty, {"nv/x": _al("a/one")})] == ["nv/x"]

    def test_alias_with_empty_target_ignored(self):
        assert apply_aliases(_UP, {"nv/x": _al("")}) == _UP


class TestBuildAliasRows:
    _AL = {
        "nv/b": _al("b/two", remark="改名对外"),
        "nv/a": _al("a/one", display_name="One", keep_original=True, force_mapping=False),
    }

    def test_sorted_by_alias(self):
        assert [r["alias"] for r in build_alias_rows(self._AL)] == ["nv/a", "nv/b"]

    def test_row_shape(self):
        r = build_alias_rows(self._AL)[0]
        assert r["target_model"] == "a/one" and r["display_name"] == "One"
        assert r["keep_original"] is True and r["force_mapping"] is False

    def test_target_missing_flag(self):
        known = {"b/two"}
        rows = {r["alias"]: r for r in build_alias_rows(self._AL, known)}
        assert rows["nv/a"]["target_missing"] is True
        assert rows["nv/b"]["target_missing"] is False

    def test_target_missing_false_when_known_not_given(self):
        assert all(r["target_missing"] is False for r in build_alias_rows(self._AL))

    @pytest.mark.parametrize("kw,expect", [
        ("nv/a", ["nv/a"]),
        ("NV/A", ["nv/a"]),
        ("b/two", ["nv/b"]),          # 命中目标模型
        ("改名", ["nv/b"]),            # 命中备注
        ("nomatch", []),
        ("", ["nv/a", "nv/b"]),
    ])
    def test_search(self, kw, expect):
        assert [r["alias"] for r in build_alias_rows(self._AL, None, kw)] == expect

    @pytest.mark.parametrize("aliases", [None, {}])
    def test_empty(self, aliases):
        assert build_alias_rows(aliases) == []


class TestIsCallBlocked:
    """开关语义：关=仅列表不显示（放行调用）；开=隐藏的模型返回 400"""

    def _seed(self, overrides, block):
        import time
        _cache["overrides"] = overrides
        _cache["aliases"] = {}                    # get_snapshot 现在返回三元组
        _cache["block"] = block
        _cache["expires"] = time.time() + 300     # 快照未过期，不会触发查库

    def teardown_method(self):
        _cache["overrides"] = None
        _cache["aliases"] = {}
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
        ('@router.put("/models/alias", tags=["管理员"])', "set_model_alias"),
        ('@router.delete("/models/alias", tags=["管理员"])', "delete_model_alias"),
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
        "set_model_alias", "delete_model_alias",
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
        ("set_model_alias", "insert_audit("),
        ("delete_model_alias", "insert_audit("),
    ])
    def test_writes_are_audited(self, fn, audit):
        assert audit in self._body(fn)

    def test_alias_write_validation_order(self):
        # 校验顺序：字符合法 → alias ≠ target → 撞名 → target 存在。
        # 顺序错了会先报「目标不存在」这种误导性错误
        body = self._body("set_model_alias")
        for code in ("invalid_model_id", "alias_equals_target",
                     "alias_conflicts_model", "target_not_found"):
            assert f'"code": "{code}"' in body
        assert (body.index("alias_equals_target") < body.index("alias_conflicts_model")
                < body.index("target_not_found"))

    def test_alias_conflict_checked_against_all_known_models(self):
        # 撞名与 target 存在性都对照「真实存在」全量集合（含被隐藏项），不能只看可见列表
        body = self._body("_known_model_ids")
        assert "all_known_models(upstream, overrides)" in body
        assert "await _known_model_ids()" in self._body("set_model_alias")

    def test_alias_upsert_and_case_insensitive_unique(self):
        body = self._body("set_model_alias")
        assert "ON CONFLICT (alias) DO UPDATE SET" in body
        # 只改大小写视作同一条别名：先删旧写法，否则 lower(alias) 唯一索引会冲突成 500
        assert "DELETE FROM model_aliases WHERE lower(alias) = lower(%s) AND alias <> %s" in body

    def test_alias_delete_is_case_insensitive_and_404(self):
        body = self._body("delete_model_alias")
        assert "DELETE FROM model_aliases WHERE lower(alias) = lower(%s)" in body
        assert '"code": "alias_not_found"' in body
        assert "status_code=404" in body

    def test_snapshot_read_needs_no_decryption(self):
        # 覆盖表全字段明文，读取不该出现解密（与上游密钥查重形成对照）
        assert "decrypt" not in _SRC

    def test_setting_key_seeded_with_default_off(self):
        assert BLOCK_SETTING_KEY == "hidden_models_block_calls"
        assert f'"{BLOCK_SETTING_KEY}": "false"' in _DB_SRC

    def test_table_created_idempotently(self):
        assert "CREATE TABLE IF NOT EXISTS model_overrides" in _DB_SRC
        assert "idx_model_overrides_hidden" in _DB_SRC

    def test_alias_table_created_with_lower_unique_index(self):
        assert "CREATE TABLE IF NOT EXISTS model_aliases" in _DB_SRC
        # 解析大小写不敏感，若允许 NV/x 与 nv/x 并存，解析结果就不确定
        assert ("CREATE UNIQUE INDEX IF NOT EXISTS idx_model_aliases_lower "
                "ON model_aliases (lower(alias))") in _DB_SRC
        assert "idx_model_aliases_target" in _DB_SRC

    def test_alias_defaults_match_cliproxyapi_mapping(self):
        # fork → keep_original 默认关（别名替换真名）；force-mapping → 默认开（响应回写别名）
        assert "keep_original: bool = False" in _SRC
        assert "force_mapping: bool = True" in _SRC


class TestPublicApiContract:
    """白名单下线后的取数链契约"""

    @staticmethod
    def _chat_body() -> str:
        """chat_completions 函数体（切到下一个顶层定义，避免误把后文算进来）"""
        import re
        rest = _PUBLIC_SRC.split("async def chat_completions(")[1]
        m = re.search(r"\n(?:@router\.|async def |def )", rest)
        return rest[: m.start()] if m else rest

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

    def test_alias_layer_sits_after_override_layer(self):
        body = _PUBLIC_SRC.split("async def get_model_list(")[1].split("\n\n\n")[0]
        assert "apply_aliases(apply_overrides(raw, overrides), aliases)" in body

    def test_aliases_never_enter_corrector_set(self):
        # 纠错返回值会直接写进 body["model"]，别名一旦进纠错集合就可能被模糊匹配成别名
        # 再原样发给上游 → 404。纠错集合只吃真名。
        body = _PUBLIC_SRC.split("async def get_model_list(")[1].split("\n\n\n")[0]
        assert "refresh_verified_models(apply_aliases" not in body
        assert "refresh_verified_models(all_known_models(raw, overrides))" in body
        assert _PUBLIC_SRC.count("refresh_verified_models(") == 1

    def test_alias_resolved_before_corrector(self):
        # 解析必须发生在纠错之前（源码下标断言先后）
        body = self._chat_body()
        i_resolve = body.index("await resolve_alias(model)")
        i_correct = body.index("validate_and_correct_model(model)")
        assert i_resolve < i_correct

    def test_alias_resolution_rewrites_body_model(self):
        # 全链路只用真名：熔断器 key、调度分桶、请求日志都吃 body["model"]
        body = self._chat_body()
        assert 'body["model"] = real_model' in body
        assert "model = real_model" in body
        assert 'alias_out = alias_used if (alias_used and force_map) else ""' in body

    def test_alias_out_threaded_to_handlers(self):
        assert "alias_out=alias_out" in _PUBLIC_SRC
        for fn in ("_handle_stream_request", "_handle_nonstream_request"):
            body = _PUBLIC_SRC.split(f"async def {fn}(")[1].split("\n\n\n")[0]
            assert 'alias_out: str = ""' in body

    def test_stream_rewrite_inside_json_success_branch(self):
        # 回写只能落在 json.loads 成功分支内，非 JSON 行必须原样透传
        body = _PUBLIC_SRC.split("async def _handle_stream_request(")[1].split("\n\n\n")[0]
        i_load = body.index("data = json.loads(line[6:])")
        i_rewrite = body.index('data["model"] = alias_out')
        i_except = body.index("except json.JSONDecodeError:")
        assert i_load < i_rewrite < i_except

    def test_nonstream_rewrite_before_logging(self):
        # 日志里的 response_body 要与下游实际收到的一致
        body = _PUBLIC_SRC.split("async def _handle_nonstream_request(")[1].split("\n\n\n")[0]
        assert body.index('data["model"] = alias_out') < body.index("_log_request(")

    def test_embeddings_resolves_alias(self):
        # 本端点没有纠错步骤，别名不解析就会被原样发给上游 → 404
        body = _PUBLIC_SRC.split("async def embeddings(")[1].split("\n\n\n")[0]
        assert "await resolve_alias(model)" in body
        assert "alias_out=alias_out" in body
        emb = _PUBLIC_SRC.split("async def _call_upstream_embeddings(")[1].split("\n\n\n")[0]
        assert 'data["model"] = alias_out' in emb

    def test_known_check_uses_all_known_models(self):
        # 对照可见列表会把「被隐藏」和「被别名替换掉的真名」都误报成未知模型
        body = self._chat_body()
        assert "all_known_models(await fetch_upstream_models(), await get_overrides())" in body

    def test_enrich_looks_up_catalog_by_real_name(self):
        # 别名条目要能继承真模型的目录元数据；管理员填的 display_name 优先
        body = _PUBLIC_SRC.split("def _enrich_model_list(")[1].split("\n\n\n")[0]
        assert "alias_real_map()" in body
        assert "lookup_id = alias_real.get(model_id) or model_id" in body
        assert "NIM_MODEL_CATALOG.get(lookup_id)" in body
        assert "if custom_display_name:" in body

    def test_probe_resolves_alias_before_direct_upstream_call(self):
        # 模型测试页的列表与下游一致（可能是别名），而 probe 直连上游
        body = _TEST_SRC.split("async def probe_model(")[1].split("\n\n\n")[0]
        assert "await resolve_alias(model)" in body
        assert '"model": real_model' in body
        assert '"upstream_model": real_model' in body

    def test_chat_path_blocks_disabled_model(self):
        assert "if await is_call_blocked(model):" in _PUBLIC_SRC
        assert '"code": "model_disabled",' in _PUBLIC_SRC
        # 判定发生在别名解析之后，入参已是真名——用别名调用绕不过「隐藏即禁用」
        body = self._chat_body()
        assert body.index("await resolve_alias(model)") < body.index("await is_call_blocked(model)")

    def test_public_endpoints_use_override_layer(self):
        # /v1/models 与 /api/v1/models 共用 list_models（双装饰器），/api/public/models 独立一份，
        # 三个对外路径都必须取覆盖层结果，不能直接拿上游全量
        assert '@router.get("/v1/models"' in _PUBLIC_SRC
        assert '@router.get("/api/v1/models"' in _PUBLIC_SRC
        for fn in ("list_models", "public_models"):
            body = _PUBLIC_SRC.split(f"async def {fn}(")[1].split("\n\n\n")[0]
            assert "models = await get_model_list()" in body
            assert "fetch_upstream_models()" not in body

    def test_unknown_model_still_forwarded(self):
        # 上游随时可能上新模型：名字不在列表里只记日志，不拦截
        assert "不拦截，仅记录日志" in _PUBLIC_SRC
