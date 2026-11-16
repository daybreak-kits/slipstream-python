from l04_repo.search import LIKE_ESCAPE, like_contains, like_pattern


class _RecordingColumn:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str | None]] = []

    def ilike(self, pattern: str, escape: str | None = None) -> str:
        self.calls.append((pattern, escape))
        return "ilike-expression"


def test_like_pattern_wraps_the_needle():
    assert like_pattern("gpt") == "%gpt%"
    assert like_pattern("  gpt-4o  ") == "%gpt-4o%"


def test_like_pattern_escapes_like_wildcards():
    assert like_pattern("100%") == "%100\\%%"
    assert like_pattern("a_b") == "%a\\_b%"


def test_like_pattern_escapes_the_escape_character_first():
    assert like_pattern("c:\\temp") == "%c:\\\\temp%"


def test_like_contains_uses_the_escape_character():
    column = _RecordingColumn()
    assert like_contains(column, "50%") == "ilike-expression"
    assert column.calls == [("%50\\%%", LIKE_ESCAPE)]
