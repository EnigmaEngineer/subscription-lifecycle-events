# Subscription lifecycle events

A webhook endpoint that takes Stripe shaped subscription events, verifies the signature,
refuses to store the same event twice and keeps the raw bytes in an append only log. A
generator produces the lifecycle stream it feeds on, including the duplicates and the
out of order arrivals a real sender delivers.

```
python scripts/measure.py
```

## What is here

```
                 signed request
                       |
            +----------v-----------+
            | signature.verify     |   hmac sha256 over "t.body"
            | tolerance 300s       |   every v1 value, every configured secret
            +----------+-----------+
                       |  400 on any refusal, because identical bytes
                       |  will not verify on a retry either
            +----------v-----------+
            | envelope shape check |   id, type, created. nothing deeper
            +----------+-----------+
                       |
            +----------v-----------+
            | EventStore.accept    |   one transaction
            |                      |
            |  deliveries  <-------+---  idempotency ledger, keyed on event id
            |  events      <-------+---  append only, raw bytes, never updated
            +----------------------+
                       |
              new / duplicate / conflict
```

`subevents/signature.py` is the only part that touches the secret. It parses the header,
checks the replay window and compares digests in constant time. It never looks at the body
as anything other than bytes.

`subevents/store.py` holds both tables. They are in one file because the append and the
dedupe entry have to commit together. A crash between them leaves either an event nobody
can dedupe or a ledger row for an event nobody stored.

`subevents/generator.py` produces the population. `scripts/gen_events.py` writes it out.

## Measured on 2026-11-18

Python 3.10.12 on this machine. Every number below is printed by `scripts/measure.py` and
`tests/test_readme.py` fails if one of them drifts.

```
fixtures                  23
handler matches fixture   23
clean events              1501
wire deliveries           1627
accepted                  1501
duplicate                 126
stored events             1501
rank max displacement     17
raw max displacement      135
tied seconds              73
events in ties            146
```

1,627 deliveries carrying 1,501 distinct events leave 1,501 rows. That is the idempotency
claim and it is measured over the generated stream rather than asserted.

The fixture corpus is 23 signed requests. 5 of them verify. Three of those five are the same
body under different headers, and delivering the whole corpus to one endpoint leaves one row
in the event log. The signature header is not part of an event's identity.

## The two displacement figures

`rank max displacement` is 17 and `raw max displacement` is 135 over the same stream. The
first version of this repository published the second one and called it the reordering
bound, which was wrong.

The wire stream carries 126 duplicates. Every copy inserted pushes all the events after it
one place further down, so an event near the end reads as displaced by 126 without having
moved at all. `rank` compares first arrivals against first arrivals and describes the
shuffle. `raw` keeps the index view because it is true about what a consumer reads, and it
is useless as a measure of reordering. Both are published so neither can be quoted as the
other.

## Verifying against something other than itself

The test suite signs with `subevents.signature` and verifies with the same module. That
proves the two halves agree and says nothing about Stripe.

```
python scripts/agree.py
```

```
OpenSSL 3.0.2 15 Mar 2022 (Library: OpenSSL 3.0.2 15 Mar 2022)
60 comparisons, 0 disagreements
```

One digest from that run is pinned as a literal in `tests/test_signature.py`. It is the only
vector in the suite that did not come out of the module it grades.

## Running the tests

`pytest` is not on the system interpreter here, so it installs to a target.

```
export PYTHONPATH=/tmp/py310
python3 -m pytest -q
```

```
93 passed
```

## The secrets this repository does not contain

The first version of this put three invented signing keys in three files, shaped like real
Stripe webhook secrets because that felt like the realistic thing to do. GitHub's scanner
opened three alerts within minutes of the first push.

Nothing was exposed. The values were made up here and were never live anywhere. The alert
was still right about the thing it checks, and a string that looks like a credential in a
public repository costs something whether or not it opens a door.

Two things changed. Secrets are read from the environment in `subevents/config.py` and are
typed nowhere else. The fixture defaults are deliberately not in any provider's format,
because HMAC does not care what a key looks like and there was never a reason for a test
key to be shaped like a live one.

```
export SUBEVENTS_WEBHOOK_SECRETS=whsec_first,whsec_second
```

`tests/test_no_secrets.py` walks every file in the repository and refuses five credential
formats. It also asserts that each of those five patterns matches a synthetic example,
because a scanner that matches nothing passes on an exposed key. Fixing three instances by
hand would have left the fourth to be found by GitHub again.

## Decisions worth arguing with

**A signature refusal returns 400 and not 401.** Stripe retries anything that is not a 2xx.
A body whose bytes failed verification once will fail every time, so a retry buys nothing
and the endpoint would rather be told to stop.

**A signed body that will not parse also returns 400.** The sender is who they claim to be
and the bytes are still unusable. Returning 500 would have Stripe retry identical bytes for
three days.

**Same id with different bytes is a conflict, not a duplicate.** A Stripe retry is byte
identical. Two different bodies under one id means something upstream is wrong, so the
second body is not stored, the request is acknowledged and a counter goes up. Acknowledged
because retrying will not resolve it either.

**An event type outside the handled set is acknowledged and dropped.** A 500 there would
make Stripe retry an event this platform is never going to want.

**Upper case hex is refused.** Stripe sends lower case. Accepting both would make this
endpoint more lenient than the sender, which is a strange direction for a signature check.

**The replay window is inclusive at 300 seconds.** Stripe's own default is five minutes and
a longer window here would be a weaker check than the sender assumes.

## Known limitations

- **Nothing has run against Stripe.** The fixtures are signed with the documented algorithm
  and checked against OpenSSL. No request from Stripe has ever reached this code.
- **The generator is not Stripe.** Billing periods are a flat thirty days, which is wrong
  for any revenue figure. Nothing here reports one.
- **Events sharing a second have no total order.** The generator plants 73 of them,
  covering 146 events, on purpose. The handler stores them and does not resolve them.
  Working that out is the next piece of work.
- **The event log has no reader yet.** State reconstruction is not built, so the claim that
  a shuffled replay equals a clean one is untested.
- **sqlite, and one process.** `BEGIN IMMEDIATE` makes the two inserts atomic against
  another thread in this process. It is not the concurrency story a real endpoint needs.
- **The ledger has no retention window.** Stripe stops retrying after about three days and
  the dedupe table keeps every id forever. 200 distinct ids leave 200 rows and there is a
  test that says so.
- **An envelope with no `data` is accepted.** The shape check reads the three fields the
  handler itself uses. A consumer reading `data.object` has to handle its absence and
  nothing downstream does yet.
- **No HTTP layer.** `handle()` takes bytes and a header. There is no server, no timeout
  and no backpressure.
- **The fixture keys are not Stripe shaped, on purpose.** See below. A real deployment
  sets `SUBEVENTS_WEBHOOK_SECRETS` and a deployment that forgets gets keys that cannot
  verify anything Stripe sent, which fails on the first delivery rather than quietly
  accepting it.
