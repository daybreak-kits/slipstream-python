"""Tests for the FOFA-style search query language.

Two properties matter more than any single operator: a keyword that happens to
look like a field ("NO_AVAILABLE_CHANNEL", "minimax-m3.1-flash") must stay a
keyword, and anything that goes through the grammar must never reach the
database as text. The injection cases at the end are the second property.
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    Float,
    Integer,
    MetaData,
    String,
    Table,
    create_engine,
    insert,
    select,
)

import l03_domain.query_lang.fields as fields_module
from l01_core.errors import ErrorCode
from l03_domain.query_lang import (
    DEFAULT_SEARCH_ATTEMPT_PATHS,
    DEFAULT_SEARCH_COLUMNS,
    FIELD_ALIASES,
    FIELDS,
    MAX_DEPTH,
    MAX_PREDICATES,
    MAX_QUERY_LENGTH,
    And,
    AttemptMatch,
    Bareword,
    Comparison,
    Not,
    Or,
    QueryError,
    compile_query,
    count_predicates,
    depth,
    has_syntax,
    iter_predicates,
    parse,
    parse_query,
)

_STRING_COLUMNS = (
    "request_id",
    "upstream_request_id",
    "requested_model",
    "channel_name",
    "upstream_model",
    "response_model",
    "api_key_label",
    "api_key_id",
    "user_id",
    "error_code",
    "error_message",
    "final_channel_model_id",
    "source",
)

_METADATA = MetaData()
_TABLE = Table(
    "request_logs",
    _METADATA,
    Column("id", Integer, primary_key=True),
    *[Column(name, String) for name in _STRING_COLUMNS],
    Column("http_status", Integer),
    Column("is_stream", Boolean),
    Column("total_ms", Integer),
    Column("ttft_ms", Integer),
    Column("ttfa_ms", Integer),
    Column("cost_usd", Float),
    Column("created_at", BigInteger),
    Column("prompt_tokens", Integer),
    Column("completion_tokens", Integer),
)

COLUMNS = {column.name: column for column in _TABLE.columns}
# The registry names the column tokens_total; the caller owns the sum.
COLUMNS["tokens_total"] = _TABLE.c.prompt_tokens + _TABLE.c.completion_tokens

FIXED_NOW = 1_800_000_000_000
SHANGHAI = ZoneInfo("Asia/Shanghai")


def sql_of(text: str, **kwargs: object) -> tuple[str, dict[str, object]]:
    compiled = compile_query(parse(text), COLUMNS, **kwargs).compile()
    return str(compiled), dict(compiled.params)


def parse_error(text: str) -> QueryError:
    with pytest.raises(QueryError) as excinfo:
        parse(text)
    return excinfo.value


def compile_error(text: str) -> QueryError:
    with pytest.raises(QueryError) as excinfo:
        compile_query(parse(text), COLUMNS)
    return excinfo.value


# --- parsing -------------------------------------------------------------


def test_parse_field_contains():
    node = parse("model=gpt-4")
    assert isinstance(node, Comparison)
    assert (node.field, node.op, node.value) == ("model", "=", "gpt-4")


def test_parse_field_names_are_case_insensitive():
    node = parse("MODEL=Gpt-4")
    assert isinstance(node, Comparison)
    # The field is normalized; the value keeps the user's casing.
    assert node.field == "model"
    assert node.value == "Gpt-4"


def test_parse_quoted_value_keeps_spaces():
    node = parse('model="gpt 4 turbo"')
    assert isinstance(node, Comparison)
    assert node.value == "gpt 4 turbo"


def test_parse_single_quoted_value():
    node = parse("model='basis'")
    assert isinstance(node, Comparison)
    assert node.value == "basis"


def test_parse_both_quote_styles_agree():
    assert parse("model='basis'") == parse('model="basis"')


def test_parse_single_quoted_value_keeps_spaces():
    node = parse("msg='upstream timeout'")
    assert isinstance(node, Comparison)
    assert node.value == "upstream timeout"


def test_parse_single_quoted_value_supports_an_escaped_quote():
    # chr(92) is a backslash; spelling it as an escape here would be read twice.
    node = parse("msg='it" + chr(92) + "'s'")
    assert isinstance(node, Comparison)
    assert node.value == "it's"


def test_parse_unclosed_single_quote_points_at_the_quote():
    error = parse_error("model='basis")
    assert error.column == 7
    assert "引号" in error.message
    assert "引号闭合" in (error.suggestion or "")


def test_parse_apostrophe_inside_a_word_stays_part_of_the_value():
    node = parse("msg=don't")
    assert isinstance(node, Comparison)
    assert node.value == "don't"


def test_parse_keyword_mode_drops_wrapping_quotes():
    assert parse("'basis'") == Bareword("basis", 1)
    assert parse('"gpt 4"') == Bareword("gpt 4", 1)


def test_parse_and_or_precedence():
    node = parse("a && b || c")
    assert isinstance(node, Or)
    assert isinstance(node.items[0], And)
    assert node.items[1] == Bareword("c", 11)


def test_parse_not_binds_tighter_than_and():
    node = parse("!a && b")
    assert isinstance(node, And)
    assert isinstance(node.items[0], Not)
    assert node.items[0].item == Bareword("a", 2)


def test_parse_parentheses_group():
    node = parse("(a || b) && c")
    assert isinstance(node, And)
    assert isinstance(node.items[0], Or)


def test_parse_double_negation():
    node = parse("!!model=a")
    assert isinstance(node, Not)
    assert isinstance(node.item, Not)


def test_parse_wildcard_operator():
    node = parse("model*=gpt*4?")
    assert isinstance(node, Comparison)
    assert node.op == "*="


def test_parse_bareword_keeps_spaces():
    node = parse("  foo bar  ")
    assert node == Bareword("foo bar", 1)


def test_parse_columns_are_one_based():
    node = parse("  model=a")
    assert isinstance(node, Comparison)
    assert node.column == 3
    assert node.value_column == 9


def test_parse_adjacent_expressions_are_rejected():
    error = parse_error("model=a foo")
    assert "&&" in error.message
    assert error.column == 9


def test_parse_strict_true_still_accepts_a_lone_bareword():
    assert parse_query("abc", strict=True) == Bareword("abc", 1)


def test_parse_strict_false_keeps_the_keyword_search():
    assert parse_query("a && b", strict=False) == Bareword("a && b", 1)


def test_parse_length_limit():
    error = parse_error("a" * (MAX_QUERY_LENGTH + 1))
    assert error.column == MAX_QUERY_LENGTH + 1
    assert str(MAX_QUERY_LENGTH) in error.message


def test_parse_length_limit_boundary_is_inclusive():
    assert parse("a" * MAX_QUERY_LENGTH) == Bareword("a" * MAX_QUERY_LENGTH, 1)


def test_parse_depth_limit():
    error = parse_error("!" * (MAX_DEPTH + 1) + "model=a")
    assert error.column == MAX_DEPTH + 2
    assert str(MAX_DEPTH) in error.message


def test_parse_depth_limit_boundary_is_inclusive():
    assert isinstance(parse("!" * MAX_DEPTH + "model=a"), Not)


def test_parse_predicate_limit():
    text = " && ".join(f"model=m{index}" for index in range(MAX_PREDICATES + 1))
    error = parse_error(text)
    assert str(MAX_PREDICATES) in error.message


def test_parse_predicate_limit_boundary_is_inclusive():
    text = " && ".join(f"model=m{index}" for index in range(MAX_PREDICATES))
    assert count_predicates(parse(text)) == MAX_PREDICATES


# --- syntax errors carry a position -------------------------------------


def test_parse_unclosed_quote_points_at_the_quote():
    error = parse_error('model="unclosed')
    assert error.column == 7
    assert "引号" in error.message


def test_parse_unknown_field_suggests_the_closest_name():
    # 未知字段名不判语法（task-126）；严格模式仍报错并给出最接近的主名。
    with pytest.raises(QueryError) as excinfo:
        parse_query("modle=x", strict=True)
    assert "model" in (excinfo.value.suggestion or "")
    assert excinfo.value.column == 1


def test_parse_unknown_field_still_lists_the_fields():
    # 同上：未知字段自动判定时走关键词；严格模式才列出可用字段。
    with pytest.raises(QueryError) as excinfo:
        parse_query("nope=x", strict=True)
    assert "可用字段" in (excinfo.value.suggestion or "")


def test_parse_missing_value_at_end_points_past_the_operator():
    error = parse_error("model=")
    assert error.column == len("model=") + 1
    assert "缺少值" in error.message


def test_parse_missing_value_before_and_points_at_the_connector():
    error = parse_error("model= && status==200")
    assert error.column == 8


def test_parse_empty_query():
    error = parse_error("   ")
    assert error.column == 1


def test_parse_empty_parentheses():
    # 自动判定不再把裸括号当语法（task-126）；严格模式仍拒绝空括号。
    with pytest.raises(QueryError) as excinfo:
        parse_query("()", strict=True)
    assert excinfo.value.column == 2
    assert "括号" in excinfo.value.message


def test_parse_unclosed_parenthesis_points_at_the_opening():
    error = parse_error("(model=a")
    assert error.column == 1
    assert "没有闭合" in error.message


def test_parse_extra_closing_parenthesis():
    error = parse_error("model=a)")
    assert error.column == 8


def test_parse_operator_without_a_field():
    # 裸操作符不再判成语法（task-126）；严格模式仍要求字段名在前。
    with pytest.raises(QueryError) as excinfo:
        parse_query("== 400", strict=True)
    assert excinfo.value.column == 1


def test_parse_trailing_operator():
    error = parse_error("model=a &&")
    assert error.column == len("model=a &&") + 1


def test_parse_rejects_contains_on_a_numeric_field():
    error = parse_error("status=gpt")
    assert error.column == 7
    assert "不支持" in error.message


def test_parse_rejects_ordering_on_a_text_field():
    error = parse_error("model>5")
    assert error.column == 6


def test_parse_error_looks_like_an_app_error():
    error = parse_error("model=")
    assert error.code == ErrorCode.INVALID_REQUEST
    assert error.status == 400
    assert error.details["column"] == error.column


# --- has_syntax ----------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "NO_AVAILABLE_CHANNEL",
        "minimax-m3.1-flash",
        "model-a",
        "gpt 4 turbo",
        "R&D",
        "a_b%c",
        "user@example.com",
        "路径/测试",
        "429 (Too Many Requests)",
        "a=b",
        "(a)",
        "!a",
    ],
)
def test_has_syntax_is_false_for_keywords(text: str):
    assert has_syntax(text) is False
    assert isinstance(parse(text), Bareword)


@pytest.mark.parametrize(
    "text",
    [
        "a&&b",
        "a || b",
        "foo && bar",
        "status>500",
        "status >= 400",
        "model=a",
        "model*=x",
        "status<=400",
        "upstream_model=x",
        "requested_model*=basis",
        "http_status==500",
        "time>7d",
    ],
)
def test_has_syntax_is_true_for_grammar(text: str):
    assert has_syntax(text) is True


@pytest.mark.parametrize("text", ['"a=b"', '"x && y"', '"!important"', "'a=b'"])
def test_has_syntax_ignores_quoted_text(text: str):
    assert has_syntax(text) is False


def test_has_syntax_empty_text_is_false():
    assert has_syntax("") is False


@pytest.mark.parametrize(("alias", "canonical"), sorted(FIELD_ALIASES.items()))
def test_real_column_names_resolve_to_the_canonical_field(alias: str, canonical: str):
    node = parse(f"{alias}==x")
    assert isinstance(node, Comparison)
    # 别名解析即归一：谓词、编译、帮助表只认主名（task-126）。
    assert node.field == canonical


def test_alias_and_main_name_produce_identical_predicates():
    # 列位置随写法不同，谓词三元组必须一致。
    for alias_text, main_text in (
        ("upstream_model==MiniMax", "upstream==MiniMax"),
        ("requested_model*=basis", "model*=basis"),
        ("http_status>=500", "status>=500"),
    ):
        via_alias, via_main = parse(alias_text), parse(main_text)
        assert (via_alias.field, via_alias.op, via_alias.value) == (
            via_main.field,
            via_main.op,
            via_main.value,
        )


def test_has_syntax_accepts_every_alias():
    for alias in FIELD_ALIASES:
        assert has_syntax(f"{alias}=x") is True


def test_typoed_alias_still_suggests_the_canonical_name():
    # 别名拼错在严格解析时报错；建议表按主名展示，不按别名。
    with pytest.raises(QueryError) as excinfo:
        parse_query("requested_modle=x", strict=True)
    suggestion = excinfo.value.suggestion or ""
    # 建议的是主名 model，而不是别名 requested_model（帮助表只按主名展示）。
    assert "model" in suggestion
    assert "requested_model" not in suggestion


# --- compiled expressions -------------------------------------------------


def test_compile_contains_uses_escaped_like():
    sql, params = sql_of("model=gpt-4")
    assert list(params.values()) == ["%gpt-4%"]
    assert "requested_model" in sql
    assert "ESCAPE" in sql.upper()


def test_compile_percent_and_underscore_stay_literal():
    _, percent = sql_of('model="100%"')
    assert list(percent.values()) == ["%100\\%%"]
    _, underscore = sql_of("model=a_b")
    assert list(underscore.values()) == ["%a\\_b%"]


def test_compile_wildcard_translates_star_and_question():
    _, params = sql_of("model*=gpt*4?")
    assert list(params.values()) == ["%gpt%4_%"]


def test_compile_wildcard_keeps_percent_literal():
    _, params = sql_of("model*=100%")
    assert list(params.values()) == ["%100\\%%"]


def test_compile_equality_is_case_insensitive():
    sql, params = sql_of("model==GPT-4")
    assert "lower(" in sql
    assert list(params.values()) == ["gpt-4"]


def test_compile_inequality_counts_empty_values_as_not_equal():
    sql, params = sql_of("model!=gpt-4")
    assert "IS NULL" in sql
    assert "!=" in sql
    assert list(params.values()) == ["gpt-4"]


def test_compile_numeric_inequality_also_counts_empty_values():
    sql, params = sql_of("latency!=500")
    assert "IS NULL" in sql
    assert list(params.values()) == [500]


def test_compile_ordering_stays_three_valued():
    sql, _ = sql_of("latency<3s")
    assert "IS NULL" not in sql


def test_compile_numeric_comparison_uses_a_bound_parameter():
    sql, params = sql_of("status>=400")
    assert "http_status" in sql
    assert list(params.values()) == [400]


def test_compile_duration_suffixes():
    _, latency = sql_of("latency>2s")
    assert list(latency.values()) == [2000]
    _, ttft = sql_of("ttft<=1500")
    assert list(ttft.values()) == [1500]
    sql, ttat = sql_of("ttat<1s")
    assert list(ttat.values()) == [1000]
    assert "ttfa_ms" in sql


def test_compile_tokens_uses_the_sum_expression():
    sql, params = sql_of("tokens>1000")
    assert "prompt_tokens" in sql and "completion_tokens" in sql
    assert list(params.values()) == [1000]


def test_compile_cost_accepts_a_dollar_prefix():
    _, params = sql_of("cost>=$0.5")
    assert list(params.values()) == [0.5]


def test_compile_time_relative_uses_compile_time_now(monkeypatch):
    monkeypatch.setattr(fields_module, "now_ms", lambda: FIXED_NOW)
    _, params = sql_of("time>7d")
    assert list(params.values()) == [FIXED_NOW - 7 * 86_400_000]


def test_compile_time_absolute_reads_local_midnight():
    expected = int(datetime(2026, 11, 1, tzinfo=SHANGHAI).timestamp() * 1000)
    _, params = sql_of("time>=2026-11-01")
    assert list(params.values()) == [expected]


def test_compile_time_absolute_with_a_clock():
    expected = int(datetime(2026, 11, 1, 12, 30, tzinfo=SHANGHAI).timestamp() * 1000)
    _, params = sql_of("time<2026-11-01T12:30")
    assert list(params.values()) == [expected]


def test_compile_time_rejects_unknown_values():
    error = compile_error("time>yesterday")
    assert "时间值" in error.message
    assert error.column == 6


def test_compile_bool_fields():
    sql_true, _ = sql_of("stream==true")
    assert "IS true" in sql_true
    # "stream != false" is just "stream == true"; both spellings are accepted.
    sql_not_false, _ = sql_of("stream!=false")
    assert "IS true" in sql_not_false
    sql_false, _ = sql_of("stream!=true")
    assert "IS false" in sql_false


def test_compile_negation_and_or():
    sql, params = sql_of("!model=a || status<500")
    assert "NOT LIKE" in sql
    assert set(params.values()) == {"%a%", 500}


def test_compile_bareword_searches_every_default_column():
    _, params = sql_of("NO_AVAILABLE_CHANNEL")
    assert len(params) == len(DEFAULT_SEARCH_COLUMNS)
    assert set(params.values()) == {"%NO\\_AVAILABLE\\_CHANNEL%"}


def test_compile_bareword_keeps_spaces():
    _, params = sql_of("foo bar")
    # One term, searched across every default column, inner spaces intact.
    assert set(params.values()) == {"%foo bar%"}


def test_compile_bareword_columns_can_be_narrowed():
    _, params = sql_of("NO_AVAILABLE_CHANNEL", search_columns=["request_id"])
    assert len(params) == 1


def test_compile_missing_column_is_a_caller_bug():
    with pytest.raises(ValueError):
        compile_query(parse("model=a"), {})


def test_compile_rejects_an_unknown_field_in_a_hand_built_tree():
    node = Comparison(field="nope", op="=", value="x", column=1, value_column=6)
    with pytest.raises(QueryError):
        compile_query(node, COLUMNS)


def test_compile_type_error_points_at_the_value():
    error = compile_error("status>=abc")
    assert error.column == 9


def test_every_field_maps_to_a_column():
    missing = [field.column for field in FIELDS.values() if field.column not in COLUMNS]
    assert missing == []


def test_operator_sets_follow_the_field_kind():
    assert FIELDS["model"].operators == {"==", "!=", "=", "*="}
    assert "=" not in FIELDS["status"].operators
    assert FIELDS["stream"].operators == {"==", "!="}


def test_not_equal_includes_empty_rows_and_ordering_excludes_them():
    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        _METADATA.create_all(connection)
        connection.execute(
            insert(_TABLE),
            [
                {"id": 1, "error_message": None, "total_ms": None},
                {"id": 2, "error_message": "timeout", "total_ms": 5000},
                {"id": 3, "error_message": "other", "total_ms": 100},
            ],
        )

        def ids(text: str) -> list[int]:
            expression = compile_query(parse(text), COLUMNS)
            statement = select(_TABLE.c.id).where(expression).order_by(_TABLE.c.id)
            return [row.id for row in connection.execute(statement)]

        # A row with no message matches "message is not timeout"; a row with no
        # latency does NOT match "latency < 3s" (ordering stays three-valued).
        assert ids("msg!=timeout") == [1, 3]
        assert ids("latency<3s") == [3]
    engine.dispose()


# --- attempt trail seam ---------------------------------------------------


class RecordingResolver:
    def __init__(self, answer: bool = True) -> None:
        self.seen: list[AttemptMatch] = []
        self.answer = answer

    def matches(self, match: AttemptMatch):
        self.seen.append(match)
        if not self.answer:
            return None
        return _TABLE.c.id.is_not(None)


def test_compile_attempt_field_carries_the_json_paths():
    resolver = RecordingResolver()
    sql, _ = sql_of("channel=MiniMax", attempts=resolver)
    assert len(resolver.seen) == 1
    assert resolver.seen[0].paths == ("$.channel_name", "$.channel_slug")
    assert resolver.seen[0].value == "MiniMax"
    assert "request_logs.id" in sql


def test_compile_attempt_hit_is_ored_with_the_row_column():
    resolver = RecordingResolver()
    sql, _ = sql_of("error=NO_AVAILABLE_CHANNEL", attempts=resolver)
    assert " OR " in sql.upper()
    assert resolver.seen[0].paths == ("$.error_code",)


def test_compile_attempt_resolver_without_an_answer_degrades():
    resolver = RecordingResolver(answer=False)
    sql, _ = sql_of("channel=MiniMax", attempts=resolver)
    assert "request_logs.id" not in sql
    assert resolver.seen[0].field == "channel"


def test_compile_bareword_attempt_search_uses_default_paths():
    resolver = RecordingResolver()
    sql_of("NO_AVAILABLE_CHANNEL", attempts=resolver)
    assert resolver.seen[0].paths == DEFAULT_SEARCH_ATTEMPT_PATHS
    assert resolver.seen[0].field == ""


def test_compile_without_a_resolver_keeps_the_row_columns():
    sql, params = sql_of("channel=a")
    assert "channel_name" in sql
    assert set(params.values()) == {"%a%"}


# --- hooks seam -----------------------------------------------------------


class RecordingHooks:
    def __init__(self) -> None:
        self.contains_calls: list[str] = []
        self.wildcard_calls: list[str] = []

    def contains(self, column, text):
        self.contains_calls.append(text)
        return column.ilike(f"%{text}%")

    def wildcard(self, column, pattern):
        self.wildcard_calls.append(pattern)
        return column.ilike(f"%{pattern}%")


def test_compile_hooks_are_injectable():
    hooks = RecordingHooks()
    compile_query(parse("model=a && channel*=b*"), COLUMNS, hooks)
    assert hooks.contains_calls == ["a"]
    # channel has a second backing column (final channel-model id), so the
    # wildcard hook is asked once per column.
    assert hooks.wildcard_calls == ["b*", "b*"]


# --- injection ------------------------------------------------------------


def test_compile_keeps_a_quoted_payload_in_one_bound_parameter():
    sql, params = sql_of("model==\"x' OR '1'='1\"")
    assert "OR 1" not in sql.upper()
    # Equality is case-insensitive, so the comparison value is lowercased --
    # still one bound parameter, never SQL text.
    assert list(params.values()) == ["x' or '1'='1"]


def test_compile_escapes_wildcards_inside_a_payload():
    _, params = sql_of('model="50%_done"')
    assert list(params.values()) == ["%50\\%\\_done%"]


def test_unquoted_injection_payload_is_a_syntax_error():
    error = parse_error("model=' OR 1=1 --")
    assert error.code == ErrorCode.INVALID_REQUEST


# --- tree helpers ---------------------------------------------------------


def test_count_predicates_and_depth():
    node = parse("(a || b) && !c")
    assert count_predicates(node) == 3
    assert depth(node) == 3


def test_iter_predicates_is_in_source_order():
    node = parse("a && b || c")
    assert [item.text for item in iter_predicates(node)] == ["a", "b", "c"]
