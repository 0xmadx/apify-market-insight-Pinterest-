"""The unit the scraper yields.

A plain dict would be simpler, but then dedup has no idea what the record's
identity is or which scope it belongs to, and every scraper would have to
re-implement that. `Record` carries the three things the incremental layer needs
and nothing else.

    yield Record(scope="pins", id=pin["id"], data={...})

  scope  which seen-set this belongs to. Keep them narrow — "pins",
         "search:christmas ornament", "trends:US". A scope is what you would want
         to reset independently.
  id     stable identity within that scope. If Pinterest's id is absent, pass
         None: the record then always counts as new rather than being dropped,
         because a missing id is a parser bug, not a duplicate.
  data   the dict that lands in the Apify dataset. Only this is pushed.
  fields optional subset of `data` keys that decide "has this changed". Pass the
         metric keys so a record re-pulls when a number moves but not when
         Pinterest reorders a list or adds a field nothing reads.
"""
from dataclasses import dataclass, field
from typing import Optional, Sequence


@dataclass
class Record:
    scope: str
    id: Optional[str]
    data: dict
    fields: Optional[Sequence[str]] = field(default=None)
