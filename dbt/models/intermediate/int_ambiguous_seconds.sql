-- Seconds the precedence cannot settle, per subscription.
--
-- A second holding two events of the same rank has no order anywhere in the
-- data. The sort still produces one and the answer it picks is arbitrary.
-- Counting those is the only honest thing to do with them.

with per_second as (

    select
        subscription_id,
        created,
        count(*)                  as events_in_second,
        count(distinct type_rank) as distinct_ranks
    from {{ ref('int_event_order') }}
    group by 1, 2

)

select
    subscription_id,
    count(*) as ambiguous_seconds
from per_second
where events_in_second > 1
  and distinct_ranks < events_in_second
group by 1
