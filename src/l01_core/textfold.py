"""One case-folding definition for every grouping axis (ADR-071, decision 14).

```fold_key``` folds ASCII letters only -- character for character what
SQLite's ```lower()``` does. Python's ```str.lower()``` folds more
(```Ä→ä```, ```İ→i̇```, ```Σ→σ```), and a mismatch splits one display
name across aggregate buckets that a ```model=``` filter answers as one, or
hides rows that filter should return. The rule everywhere: group on
```fold_key```, label with the first-seen spelling (the catalog spelling wins
when one exists).

PostgreSQL's ```lower()``` is Unicode-aware, so on that backend SQL predicates
fold slightly more than these Python keys do. That can only match *more* rows,
never mislabel one -- the safe direction. ADR-071 decision 14 records why v1
does not fold Unicode on the SQL side.
"""

from __future__ import annotations

_ASCII_LOWER = str.maketrans(
    "ABCDEFGHIJKLMNOPQRSTUVWXYZ",
    "abcdefghijklmnopqrstuvwxyz",
)


def fold_key(value: object | None) -> str:
    """The grouping key for a display name: ASCII-only ```lower()```.

    Deliberately *not* ```str.lower()```: that would fold ```ÄBC``` and
    ```äbc``` into one key while ```lower('ÄBC') = 'Äbc' != 'äbc'``` in
    SQLite, so the aggregate bucket and the ```model=``` predicate would
    disagree. Non-ASCII letters pass through untouched, exactly like SQLite.
    """
    if value is None:
        return ""
    return str(value).translate(_ASCII_LOWER)
