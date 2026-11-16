"""ADR-071 decision 14: one fold definition across every grouping axis.

```fold_key``` folds ASCII letters only -- exactly what SQLite's ```lower()```
does. ASCII twins must merge into one bucket; non-ASCII twins must NOT merge,
because ```lower('ÄBC') == 'Äbc' != 'äbc'``` and ```lower('İST') == 'İst'```
in SQLite. A Python-side ```str.lower()``` would merge them, and then the
aggregate bucket disagrees with the ```model=``` predicate built on SQL
```lower()``` -- the reported bug's mirror image.
"""

from __future__ import annotations

import json

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from l01_core.textfold import fold_key
from l02_infra.db import Base
from l04_repo.orm import ChannelModelORM, ChannelORM, ComboORM, RequestLogORM
from l05_service.channel_axis import channel_axis, model_axis
from l05_service.flows import _channel_node, _model_node, _upstream_node
from l05_service.log_views import combo_groups, status_segments
from l05_service.timeseries import _by_channel, _by_dimension, _named_totals

MODEL = "MiniMax-M3.1-Flash"


def _row(index: int, *, requested: str = MODEL, upstream: str = "", channel: str = "ch-a") -> RequestLogORM:
    return RequestLogORM(
        id=f"log-{index}",
        request_id=f"req-{index}",
        requested_model=requested,
        upstream_model=upstream or requested,
        response_model=upstream or requested,
        channel_name=channel,
        attempts_json="[]",
        http_status=200,
        created_at=1_760_000_000_000 + index,
        prompt_tokens=1,
        completion_tokens=1,
    )


# --------------------------------------------------------------------------- #
# fold_key: the SQLite lower() boundary
# --------------------------------------------------------------------------- #


def test_fold_key_merges_ascii_twins() -> None:
    assert fold_key(MODEL) == fold_key(MODEL.lower())
    assert fold_key("ABC") == "abc"


def test_fold_key_keeps_non_ascii_pairs_apart() -> None:
    """SQLite lower() 的语义边界：只折 ASCII，非 ASCII 大小写必须不并。"""
    assert fold_key("ÄBC") != fold_key("äbc")
    assert fold_key("İSTANBUL") != fold_key("istanbul")
    assert fold_key("ΣΟΦΟΣ") != fold_key("σοφός")
    assert fold_key("Ｍodel") == "Ｍodel"  # 全角字母也不是 ASCII


def test_fold_key_handles_empty_and_non_letters() -> None:
    assert fold_key(None) == ""
    assert fold_key("") == ""
    assert fold_key("模型-1") == "模型-1"
    assert fold_key("Ch-9") == "ch-9"


# --------------------------------------------------------------------------- #
# timeseries: by_model / by_upstream_model / by_channel
# --------------------------------------------------------------------------- #


def test_by_dimension_merges_case_twins_and_keeps_first_spelling() -> None:
    series = _by_dimension([_row(0), _row(1, requested=MODEL.lower())], "requested_model", "—")
    assert len(series) == 1
    assert series[0]["name"] == MODEL
    assert series[0]["requests"] == 2


def test_by_dimension_without_case_twins_matches_the_old_grouping() -> None:
    rows = [_row(0), _row(1, requested="gpt-4o"), _row(2, requested="gpt-4o")]
    series = _by_dimension(rows, "requested_model", "—")
    assert [(item["name"], item["requests"]) for item in series] == [
        ("gpt-4o", 2),
        (MODEL, 1),
    ]


def test_named_totals_merges_case_twins_and_keeps_first_spelling() -> None:
    totals = _named_totals([_row(0), _row(1, requested=MODEL.lower())], "requested_model", "—")
    assert len(totals) == 1
    assert totals[0]["name"] == MODEL
    assert totals[0]["requests"] == 2
    assert totals[0]["prompt_tokens"] == 2


# --------------------------------------------------------------------------- #
# log_views: combo buckets and status segments
# --------------------------------------------------------------------------- #


def _view_row(index: int, requested: str) -> dict:
    return {
        "id": f"log-{index}",
        "request_id": f"req-{index}",
        "requested_model": requested,
        "created_at": 1_760_000_000_000 + index,
        "http_status": 200,
        "prompt_tokens": 1,
        "completion_tokens": 1,
        "cached_tokens": 0,
        "cost_usd": 0.0,
        "ttft_ms": 10,
        "total_ms": 20,
        "channel_name": "ch-a",
        "error_code": None,
        "attempts_json": "[]",
    }


def test_combo_groups_merge_case_twins_and_keep_first_label() -> None:
    groups = combo_groups([_view_row(0, MODEL), _view_row(1, MODEL.lower())])
    assert len(groups) == 1
    assert groups[0]["label"] == MODEL
    assert groups[0]["requests"] == 2


def test_combo_groups_without_case_twins_stay_separate() -> None:
    groups = combo_groups([_view_row(0, MODEL), _view_row(1, "gpt-4o")])
    assert sorted(group["label"] for group in groups) == [MODEL, "gpt-4o"]


def test_status_segments_do_not_break_on_case_twins() -> None:
    attempts = [
        {"at": 1_000, "upstream_model": MODEL, "status": "success", "ms": 5, "channel_name": "ch-a"},
        {
            "at": 2_000,
            "upstream_model": MODEL.lower(),
            "status": "success",
            "ms": 5,
            "channel_name": "ch-a",
        },
    ]
    row = _view_row(0, MODEL)
    row["attempts_json"] = json.dumps(attempts)
    segments = status_segments([row])
    assert len(segments) == 1
    assert segments[0]["label"] == MODEL
    assert segments[0]["attempts"] == 2


# --------------------------------------------------------------------------- #
# flows: node identity folds, labels keep the user's spelling
# --------------------------------------------------------------------------- #


def test_flow_node_ids_fold_but_labels_keep_the_user_spelling() -> None:
    upper = _row(0)
    lower = _row(1, requested=MODEL.lower())
    model_a, label_a = _model_node(upper)
    model_b, label_b = _model_node(lower)
    assert model_a == model_b
    assert {label_a, label_b} == {MODEL, MODEL.lower()}

    up_a, _ = _upstream_node(upper)
    up_b, _ = _upstream_node(_row(2, upstream=MODEL.lower()))
    assert up_a == up_b

    ch_a, _ = _channel_node(upper)
    ch_b, _ = _channel_node(_row(3, channel="CH-A"))
    assert ch_a == ch_b


# --------------------------------------------------------------------------- #
# channel axis: identity is channel_id, folded name is only the fallback
# --------------------------------------------------------------------------- #


@pytest.fixture
async def axis_session() -> AsyncSession:
    engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


async def test_channel_axis_prefers_channel_id_and_current_name(axis_session: AsyncSession) -> None:
    """渠道改过名也一样：身份是 channel_id，展示名取当前 name（ADR-071 决策 14）。"""
    axis_session.add_all(
        [
            ChannelORM(id="ch-1", slug="ch-1", name="VendorQ"),
            ChannelModelORM(id="cm-1", channel_id="ch-1", upstream_model="m", call_model="m"),
        ]
    )
    await axis_session.commit()

    fresh = _row(0, channel="VendorQ")
    fresh.final_channel_model_id = "cm-1"
    stale = _row(1, channel="VendorQ")  # 历史拼写留在行内，身份不漂移
    stale.final_channel_model_id = "cm-1"
    orphan = _row(2, channel="VendorQ")  # 无 channel-model id：名字折叠回退
    unavailable = _row(3, channel="")  # 哨兵：不是渠道名，绝不参与折叠
    # builder 从行里收集 channel-model id：空行列表 = 空目录，先攒行再解析。
    axis = await channel_axis(
        axis_session,
        [fresh, stale, orphan, unavailable],
        fallback_label="请求模型无可用渠道",
    )

    assert axis.ident_label(fresh) == ("ch-1", "VendorQ")
    assert axis.ident_label(stale) == ("ch-1", "VendorQ")
    assert axis.ident_label(orphan) == ("vendorq", "VendorQ")
    assert axis.ident_label(unavailable) == ("channel:none", "请求模型无可用渠道")

    merged = _by_channel([fresh, stale, orphan, unavailable], axis.ident_label)
    names = {item["name"]: item["requests"] for item in merged}
    # id 桶只收有 id 的行；无 id 行的名字回退桶（fold_key）不并入 id 桶——
    # 与综合模型轴「回退不回填」同一语义；哨兵永远是独立第三桶。
    assert names == {"VendorQ": 2, "VendorQ": 1, "请求模型无可用渠道": 1}
    assert fold_key("请求模型无可用渠道") != "vendorq"


async def test_model_axis_groups_by_ids_and_falls_back_by_name(
    axis_session: AsyncSession,
) -> None:
    """裁定②③：上游轴按 channel-model id、综合轴按 combo id，回退不回填。"""
    axis_session.add_all(
        [
            ChannelORM(id="ch-1", slug="ch-1", name="VendorQ"),
            ChannelModelORM(
                id="cm-1",
                channel_id="ch-1",
                upstream_model="DeepSeek-Flash",
                call_model="deepseek-flash",
            ),
            ComboORM(id="c-1", name="Pkg Duo"),
        ]
    )
    await axis_session.commit()

    upstream_only = _row(0, requested="whatever", upstream="deepseek-flash")
    upstream_only.final_channel_model_id = "cm-1"
    renamed = _row(1, requested="whatever", upstream="deepseek-flash-old")
    renamed.final_channel_model_id = "cm-1"  # 实体改过名：展示当前 upstream_model
    orphan = _row(2, requested="Basis", upstream="Basis")  # 无 id：按名折叠回退
    direct = _row(3, requested="deepseek-flash", upstream="deepseek-flash")
    direct.final_channel_model_id = "cm-1"
    direct.resolved_combo_id = "c-1"
    ghost = _row(4, requested="whatever", upstream="ghost-old")
    ghost.final_channel_model_id = "cm-gone"  # 实体被删：身份不变、展示行内快照

    rows = [upstream_only, renamed, orphan, direct, ghost]
    models = await model_axis(axis_session, rows, fallback_label="(未命名)")

    # 上游模型轴（②）：分组键 final_channel_model_id，展示名取当前上游名。
    assert models.upstream_ident(upstream_only) == ("cm-1", "DeepSeek-Flash")
    assert models.upstream_ident(renamed) == ("cm-1", "DeepSeek-Flash")
    assert models.upstream_ident(ghost) == ("cm-gone", "ghost-old")
    assert models.upstream_ident(orphan) == ("basis", "Basis")

    # 综合模型轴（③）：手动组合优先且展示组合当前名；直连回退 channel-model
    # id；历史 null 行与自动组合按名回退，绝不回填成组合 id。
    assert models.model_ident(direct) == ("combo:c-1", "Pkg Duo")
    direct.resolved_combo_id = None
    assert models.model_ident(direct) == ("cm:cm-1", "DeepSeek-Flash")
    assert models.model_ident(orphan) == ("basis", "Basis")
