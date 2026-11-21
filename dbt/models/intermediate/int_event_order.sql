-- Put the events in the order they are applied in.
--
-- Nothing in this key comes from arrival. The second an event carries decides
-- first, then a precedence over event types inside a tied second, then the
-- event id. The precedence lives in a seed generated from the handler's own
-- table so the two cannot drift.
--
-- The last step is a sort on a random string. It is here to make the order
-- total rather than because it means anything, and int_ambiguous_seconds
-- counts the cases that reach it.

with ranked as (

    select
        events.*,
        coalesce(ranks.type_rank, {{ var('unranked_type_rank') }}) as type_rank,
        coalesce(ranks.is_invoice, false)                          as is_invoice
    from {{ ref('stg_subscription_events') }} as events
    left join {{ ref('event_type_rank') }} as ranks
        on ranks.event_type = events.event_type
    where events.subscription_id is not null

)

select
    *,
    row_number() over (
        partition by subscription_id
        order by created, type_rank, event_id
    ) as apply_order
from ranked
