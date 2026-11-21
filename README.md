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
            | API Gateway HTTP API |   POST /stripe/events, authorization NONE
            | payload format 2.0   |   body as a string, plus isBase64Encoded
            +----------+-----------+
                       |
            +----------v-----------+
            | lambda_entry.raw_body|   the only place that decides what the
            |                      |   body is. everything below takes bytes
            +----------+-----------+
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
            +----------+-----------+
                       |
              new / duplicate / conflict
                       |
            +----------v-----------+
            | state.reconstruct    |   sorts on created, type rank, event id
            |                      |   never on the row sequence
            +----------------------+
                       |
              subscription state

  the same events, the other way out. nothing produces to the topic yet

            +----------------------+
            | Kafka sink           |   one row per delivery, no dedupe
            | record_content       |   record_metadata beside it
            +----------+-----------+
                       |
            +----------v-----------+
            | stg_subscription     |   one row per event id. the thing
            |   _events            |   that refuses a repeat lives here
            +----------+-----------+
                       |
            +----------v-----------+
            | subscription_state   |   the same fold, in SQL
            +----------------------+
```

`subevents/signature.py` is the only part that touches the secret. It parses the header,
checks the replay window and compares digests in constant time. It never looks at the body
as anything other than bytes.

`subevents/store.py` holds both tables. They are in one file because the append and the
dedupe entry have to commit together. A crash between them leaves either an event nobody
can dedupe or a ledger row for an event nobody stored.

`subevents/generator.py` produces the population. `scripts/gen_events.py` writes it out.

`subevents/state.py` folds the log into per subscription state. It reads the rows in
whatever order the log hands them over and sorts them on fields the events carry, which is
the only reason a shuffled delivery can land on the same answer as a clean one.

## Measured on 2026-11-21

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
bodies carried as text    22
bodies needing base64     1
outcomes a wrong decode changes  11
schema refuses            5
acknowledged and nonconforming   0
configuration rules       10 of 10
sink rules                12 of 12
warehouse rules           8 of 8
connector defaults read   16
pages disagree about      2
defaults set by version   4
warehouse names created   8
warehouse names spent     8
ledgers at which nothing is refused  4
state digests across every split     1
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

## Reconstructing state, measured on 2026-11-19

```
subscriptions             200
clean log rows            1501
wire deliveries           1627
wire log rows             1501
replays agree             yes
replay straight off wire  yes
tied seconds              73
resolved by type rank     73
resolved by event id      0
tie order changes state   0
arrival order wrong subs  102
arrival order orphans     95

probe tied seconds        329
probe ties left to the id 28
probe subs flagged        27
probe subs id order moved 6
probe moved but unflagged 0
```

The clean stream and the wire stream hold the same events. One is in event time order with
nothing repeated. The other is shuffled inside a bounded window with duplicates in it. Both
go through the real handler into a real store, both are read back out of the append only
log, and the two reconstructions are byte identical. The fold produces 200 subscriptions on
either route.

That only holds because the ordering comes out of the events. Ordering by arrival instead
gets 102 of 200 subscriptions wrong, and 95 events arrive before the subscription they
belong to exists at all.

## The tie break, and what it turned out to be worth

Stripe puts no version counter on a subscription object. 73 seconds in this corpus hold two
events for one subscription, so those two have no order anywhere in the data.

`subevents/state.py` ranks event types inside a second. An invoice event is the cause of the
subscription update that follows it, because Stripe reports the failed payment and moves the
subscription to `past_due` in the same second. That rank settles 73 of 73 ties here.

Then the rank was inverted to find out what it was worth.
Inverting it changes the state of 0 subscriptions.
Every tie in this corpus is an invoice event sitting beside a subscription update and the
two write different fields, so the order of that pair cannot be observed in the result. The
rule is written down, defended, and doing no work.

What does decide something is the step underneath it. Two events of the same rank in one
second fall through to a sort on the event id, and a Stripe id is random. The generator
carries a switch that plants that case. It is off by default because turning it on changes
every byte the generator produces.
On that arm the id sort leaves 28 seconds to a coin flip.
It moves the quantity on 6 of 200 subscriptions.

So those seconds get counted instead of resolved. `SubscriptionState.ambiguous_seconds`
reports how many of a subscription's seconds were settled by nothing. It fires wider than
the damage, because most of those seconds land on a field the next event overwrites anyway.
What matters is the other direction.
Of the subscriptions the id sort moves, 0 of them are unflagged.

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
287 tests collected
286 passed, 1 skipped
```

The skip is deliberate and it is counted. One fixture in the corpus is signed bytes that
are not valid UTF-8, so the arm that carries a body as a string has nothing to assert about
it. `tests/test_readme.py` derives the skip count from the measurement rather than
accepting whatever the run reports.

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

## Behind a gateway, measured on 2026-11-20

`infra/` declares the API Gateway HTTP API, the Lambda function behind it, an MSK
serverless cluster and a Glue Schema Registry entry for the envelope. **None of it has
been applied.** There is no AWS account behind this repository, so `terraform validate` is
the furthest this configuration has been taken. The limitations section says what that
leaves unproven.

What writing it turned up was not in the configuration. It was in the code the
configuration points at.

### The body is a string and the signature is over bytes

API Gateway does not hand a Lambda function a request. It hands over a JSON document with
the body as a string and a separate flag saying whether that string is base64. The flag is
set by the gateway and not by the sender. `subevents/lambda_entry.py` is the only place
allowed to decide what the body is, and it is a module rather than four inline lines
because the decoding rule is the part worth grading.

A decoder that ignores the flag changes the outcome of **11 of the 23** fixtures, and the
shape of that is worse than the count. Every one of the 23 comes back `rejected` under the
wrong decoder. Not fewer acceptances. No other outcome at all. Five valid deliveries, one
event this platform ignores on purpose and five malformed envelopes all report that no
signature matched, which points whoever is on call at Stripe rather than at the gateway.

One fixture in the corpus is signed bytes that are not valid UTF-8. The gateway has no way
to put those in a JSON string, so **22 of 23** bodies can arrive as text and 1 can only
ever arrive base64. The suite skips the text arm for that one and pins the count, because
a skip nobody counts is a test that stopped running.

### The idempotency ledger stops being one ledger

This is the finding worth the day. Each concurrent Lambda execution environment gets its
own `/tmp`, so each one gets its own sqlite file, so each one gets its own ledger. A retry
landing anywhere else meets a ledger that has never seen the event.

```
wire deliveries           1627
distinct events           1501
duplicate deliveries      126

round_robin dispatch
  ledgers  refused  rows  extra rows  fold dropped
        1      126  1501           0             0
        2       14  1613         112           112
        4        0  1627         126           126
        8        0  1627         126           126
       16        0  1627         126           126

random dispatch
  ledgers  refused  rows  extra rows  fold dropped
        1      126  1501           0             0
        2       62  1565          64            64
        4       34  1593          92            92
        8       16  1611         110           110
       16       10  1617         116           116

distinct state digests    1
state survives every split yes
```

Round robin is the adversarial arm rather than the gentle one, because this generator
inserts every duplicate immediately after its original and taking environments in turn
guarantees a pair never meets itself. **At 4 ledgers it refuses nothing at all.** Random
dispatch is the better model and it tracks one in k, which is what you would predict and
is worth stating because it was predicted before it was run.

The answer does not change. One state digest across all 10 arms. What holds that together
is the dedupe inside `state.reconstruct`, which was dead code until 2026-11-19, when an arm
reading straight off the wire was added to exercise it. The ledger built first is the part
that stops working, and the part nothing was exercising is the part carrying the guarantee.

### The schema and the handler disagree about one envelope

The Glue entry registers the envelope as a contract for consumers. On this corpus it
refuses 5 envelopes and the handler had already refused every one of them, so registering
it bought a contract rather than a second gate.

It does disagree in the other direction. The schema requires `data`. The shape check
deliberately does not. So an envelope with no `data` gets a 200 and a row in the log, and
then fails at the producer against the contract the registry holds. That envelope was named
as a limitation on 2026-11-18. Registering the schema turned it from a limitation into two
components answering differently about the same bytes.

### What terraform does and does not grade

Ten defects were planted into the configuration one at a time, each one matching a rule in
`subevents/infra_rules.py`. `terraform validate` caught 1 of 10. The other 9 are
configurations it called valid.

A function timeout above the gateway's integration ceiling. A Lambda permission with no
`source_arn`, which lets any API Gateway in any account invoke the function. A policy
granting every resource. A registry compatibility of `NONE`. A log group with no retention.
The payload format the decoder does not read. A variable naming a real network carrying a
default. None of those are schema errors and all of them are wrong.

So the rules live in the suite and read the files as text, which means they run on a clone
with no terraform and no credentials. The weakness of that is regular expressions over
source, and the answer to it is that every rule is also pointed at a copy carrying its own
defect and has to refuse it. A rule whose pattern stops matching reads exactly like a rule
that found nothing.

There is an eleventh planted case and it is not a rule. It misspells an attribute name,
which terraform has to refuse. A manifest reporting that validate caught nothing looks the
same whether validate is discerning or whether it never ran.

## Into the warehouse, measured on 2026-11-21

`connector/snowflake-sink.json` is the Kafka Connect configuration for the Snowflake sink
running Snowpipe Streaming. `infra-snowflake/` creates the database and its three schemas. It
also creates the warehouse, the roles and the service user the sink
authenticates as. `dbt/` reconstructs
subscription state in SQL over what the sink lands. **None of it has been applied**, for
the same reason as `infra/`. There is no Snowflake account behind this repository.

### The sink does not deduplicate, so the ledger moved

The measurement above says the sqlite ledger refuses nothing once there are four of it. The
answer turned out not to be a shared cache. It is that there is exactly one warehouse.

The sink's exactly once guarantee is about Kafka offsets rather than about Stripe event
ids. A retried delivery is a new offset carrying an id the table has already seen, so every
copy lands. 1,627 deliveries become 1,627 rows and `stg_subscription_events` returns 1,501.
Two copies of one id are ordered by the Kafka partition and the offset. That is a real
total order over deliveries and it says nothing about which copy Stripe sent first.

### The state fold, written twice

`subevents/state.py` folds the log in Python. `dbt/models/marts/subscription_state.sql`
folds it in SQL. The second one exists because the first one runs where the handler runs,
and a platform whose only reconstruction is in its handler has nothing running in the
warehouse at all.

```
export SUBEVENTS_DUCKDB=/tmp/subevents.duckdb
python scripts/warehouse.py
```

```
landed records            1627
distinct event ids        1501
subscriptions in python   200
subscriptions in sql      200
subscriptions disagreeing 0
folds agree               yes
fields disagreeing        {}
staging rows              1501
arrival control disagrees 102
flat rank control differs 61
probe ties left to the id 28
probe subs disagreeing    0
dbt nodes                 {'pass': 13, 'success': 5}
resolved on duckdb        5 models, exit 0
resolved on snowflake     5 models, exit 0
```

The two answers are byte identical over all 200 subscriptions. One sha256 either side.

That is a clean result on the first attempt, which is also what a comparison of one answer
against itself looks like. So there are two controls.

The first folds the same events in arrival order on the Python side. Arrival order was
measured as wrong for 102 of 200 subscriptions on 2026-11-19 and the comparator reports
exactly 102 from a different code path.

The second rebuilds the models with every event type at one rank. Something has to move,
or the models are not reading the precedence seed at all. 61 subscriptions move and **the
only field that moves is `ambiguous_seconds`**. So the ordering rule that changes nothing
when inverted also changes nothing when flattened. The only thing it feeds on this corpus
is the field whose job is to report that the answer was arbitrary.

A third arm plants seconds holding two events of one rank, because on the published corpus
the sort on the event id decides nothing and the comparison grades it not at all. On that
arm the id breaks 28 ties and the two folds still agree.

### Four files name the same objects and nothing read more than one

One tool creates these objects and three others spend them. `terraform validate` grades the
configuration against the provider schema and never opens the sink configuration. A dbt
parse resolves a target without connecting, so a warehouse that does not exist resolves
exactly like one that does. Neither tool is broken. The join is simply nobody's job.

`subevents/names.py` runs it in both directions over 8 names created and 8 spent. Each
direction carries a list of deliberate exceptions with a reason, and an entry in either
list that no longer has that status fails as well.

It found a defect on the day it was written. dbt appends a model's configured schema to the
profile's, so a profile schema of `ANALYTICS` and a model schema of `STAGING` resolve to
`ANALYTICS_STAGING`. Nothing complains about that. This configuration creates no such
schema and the transforming role holds no `CREATE SCHEMA`, so the first real run would have
failed on a name that appears in no file. `dbt/macros/generate_schema_name.sql` makes the
configured schema the whole name.

The interesting exception runs the other way. The sink reads its user name from a Connect
config provider along with the private key, so the one identifier nothing here can
reconcile is the one the sink authenticates as. Externalising a secret externalises the
name sitting beside it.

### What the sink does if you leave a line out

16 defaults were read off four vendor pages and each one is recorded with the sentence that
states it and the page it came from. A quote has to contain the value it is evidence for,
which is what stops a quote being picked for sounding good.

The one worth the day is that `snowflake.ingestion.method` defaults to `SNOWPIPE`. Every
other property in that file reads like a streaming configuration and one omitted line turns
the whole thing into batch loading through an internal stage, with no error anywhere.

Two properties have different documented defaults on two pages of the same documentation
set. `buffer.flush.time` is 10 on the streaming page and 120 on the install page.
`buffer.size.bytes` is 20000000 on one and 5000000 on the other. Four more depend on which
connector release the cluster happens to be running. A configuration assembled from two
pages is therefore resting on which page somebody opened. Every contested property here is
either stated outright or sits in a list of deliberate omissions with a reason.

The install page's own sample configuration is incompatible with streaming. It sets
`value.converter` to a Snowflake converter and the streaming page says Snowflake converters
are not supported there. A reader starts at the sample and switches the ingestion method on
the other page, and nothing between the two mentions it.

### What terraform grades in the warehouse module

`terraform fmt` and `terraform validate` both pass on `infra-snowflake/` and say nothing
about any of the 8 rules in `subevents/warehouse_rules.py`. Three of those came out of
`terraform providers schema -json` rather than out of a guide.

`strict_privilege_management` on a grant defaults to false, and the provider's own
description says a grant with it off is only concerned with the privileges the
configuration names. So a privilege somebody grants by hand survives every apply and the
file still reads like the whole picture. Every grant here sets it. The same description
says the first apply after setting it plans no removals, so turning it on costs two
applies to mean anything.

The landing table is declared nowhere and that is deliberate. The sink creates it on the
first delivery and the default role of the user in the sink configuration becomes its
owner, which is the vendor's sentence rather than a reading of one. That is also why the
service user declares a default role. Declaring the table here would be two owners for one
object.

## Known limitations

- **Nothing has run against Stripe.** The fixtures are signed with the documented algorithm
  and checked against OpenSSL. No request from Stripe has ever reached this code.
- **The generator is not Stripe.** Billing periods are a flat thirty days, which is wrong
  for any revenue figure. Nothing here reports one.
- **Events sharing a second have no total order.** The generator plants 73 of them,
  covering 146 events, on purpose. The type rank settles every one of them and changes
  nothing, and the case it cannot settle is two events of the same rank. Those are counted
  rather than resolved.
- **Reconstruction is a full fold every time.** It reads the whole log and rebuilds every
  subscription. That is correct and it is not what millions of events a day want. There is
  no snapshot and no incremental apply.
- **State lives in memory and is never written back.** The fold returns a dictionary. There
  is no state table, so nothing downstream can query a subscription without replaying.
- **sqlite, and one process.** `BEGIN IMMEDIATE` makes the two inserts atomic against
  another thread in this process. It is not the concurrency story a real endpoint needs.
- **The ledger has no retention window.** Stripe stops retrying after about three days and
  the dedupe table keeps every id forever. 200 distinct ids leave 200 rows and there is a
  test that says so.
- **An envelope with no `data` is accepted.** The shape check reads the three fields the
  handler itself uses. A consumer reading `data.object` has to handle its absence and
  nothing downstream does yet.
- **No HTTP layer runs here.** `handle()` takes bytes and a header, and
  `lambda_entry.handle_event()` takes a gateway event. Neither has been behind a socket.
  There is no server, no timeout and no backpressure in this repository.
- **Nothing in `infra/` has been applied.** `terraform fmt` and `terraform validate` are
  the whole verification story. No plan has run, because a plan needs credentials. So no
  resource name has been checked against a real account and no quota has been met. The
  variable validation blocks grade nothing either, because validate does not evaluate
  them. The deploy job in `.github/workflows/ci.yml` is held off with `if: false` for the
  same reason.
- **The ledger is wrong behind Lambda and the configuration deploys it anyway.** One
  sqlite file per execution environment. Measured above. The fix is a shared store and it
  is not written.
- **Nothing produces to MSK.** The cluster is declared and the publisher is a seam that
  writes nowhere. The topic name and the bootstrap endpoint are wired into the function
  environment and no record has been sent.
- **The schema is registered and not enforced at the door.** Nothing checks an envelope
  against the registry before acknowledging it, so the 200 and the contract can disagree.
  `subevents/envelope_schema.py` implements the part of draft-07 the schema file uses and
  no more, and the suite fails if the schema grows a keyword it ignores.
- **The gateway throttle numbers are a guess.** 200 burst and 500 a second. Nothing here
  has measured an arrival rate, and the ceiling is there to stop a bill rather than to
  shape traffic.
- **Nothing in `infra-snowflake/` has been applied either.** `terraform fmt` and
  `terraform validate` are the whole verification story there too. No grant has been
  created, so the claim that the minimum privilege set is enough rests on the vendor's
  page rather than on a role that failed without one.
- **No record has reached the sink.** The connector configuration is graded by rules over
  the file. Kafka Connect has a validate endpoint and reaching it needs a running worker
  and a reachable account, so nothing here has been through it.
- **The warehouse models ran on duckdb and not on Snowflake.** The SQL is written once and
  the dialect sits in one macro, and `dbt parse` against the Snowflake target resolves the
  relations the configuration creates. A parse is not an execution. No Snowflake SQL in
  this repository has run.
- **The comparison is over the generated corpus.** Two folds agreeing on 200 generated
  subscriptions is evidence about the rules they share and not about Stripe.
- **Nothing drops a landed record.** The staging model deduplicates on read, so the
  landing table grows with every retry forever. A real deployment needs a retention
  window on it and there is none.
- **The fixture keys are not Stripe shaped, on purpose.** See below. A real deployment
  sets `SUBEVENTS_WEBHOOK_SECRETS` and a deployment that forgets gets keys that cannot
  verify anything Stripe sent, which fails on the first delivery rather than quietly
  accepting it.
