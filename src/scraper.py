"""Where the Pinterest logic lands. Deliberately empty.

The operator is supplying the endpoint specs (the API notes and .md files)
separately. Nothing here should guess at a URL, a parameter name or a response
key before those arrive — a wrong-but-plausible endpoint costs more than no
endpoint, because it fails like a site change rather than like a mistake.

The contract the rest of the actor already depends on:

    run(session, task) -> iterable of dict

  session : curl_cffi Session, already wearing the leased identity's cookies,
            user agent and Chrome TLS fingerprint. Just call .get()/.post().
  task    : the actor input dict
  yields  : one dict per record, pushed straight to the Apify dataset

Three rules carry over from the parent repo and are not negotiable:

  1. Never index a raw response key. Parse through a named function, and diff the
     keys the response actually has against the keys the code reads. Seven
     modules there fetched correct data and read None out of it for months.
  2. Absent is not zero. A field that did not render is None, not 0. Let the
     consumer decide at the point of use.
  3. A failed fetch is never cached and never stored as 0.

`session.classify(response)` tells auth_expired from rate_limited from blocked —
use it rather than treating every non-200 the same way.
"""


class NotImplementedYet(RuntimeError):
    pass


def run(session, task):
    raise NotImplementedYet(
        "Pinterest endpoint logic has not been added yet. Drop the endpoint specs "
        "in and implement run() to yield one dict per record."
    )
