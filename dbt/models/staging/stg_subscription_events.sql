-- Unpack the envelope and refuse the second copy of an event.
--
-- The sink does not deduplicate. Its exactly once guarantee is about Kafka
-- offsets, which is a different claim from one row per Stripe event. A retried
-- delivery is a new offset carrying an event id the warehouse has already
-- seen, so every copy lands and this model is where the ledger lives now.
--
-- Two copies of one event id are ordered by the Kafka partition and offset.
-- That is a real total order over deliveries and it does not pretend to say
-- which copy Stripe sent first.

with landed as (

    select
        {{ record_text('record_content', 'id') }}                      as event_id,
        {{ record_text('record_content', 'type') }}                    as event_type,
        cast({{ record_text('record_content', 'created') }} as bigint)  as created,
        {{ record_text('record_content', 'data.object.object') }}       as object_kind,
        {{ record_text('record_content', 'data.object.id') }}           as object_id,
        {{ record_text('record_content', 'data.object.subscription') }} as object_subscription,
        {{ record_text('record_content', 'data.object.customer') }}     as customer_id,
        {{ record_text('record_content', 'data.object.status') }}       as status,
        {{ record_text('record_content', 'data.object.items.data[0].price.id') }} as plan,
        {{ record_text('record_content', 'data.object.items.data[0].quantity') }} as quantity_text,
        {{ record_text('record_content', 'data.object.current_period_start') }}  as period_start_text,
        {{ record_text('record_content', 'data.object.current_period_end') }}    as period_end_text,
        {{ record_text('record_content', 'data.object.cancel_at_period_end') }}  as cancel_text,
        cast({{ record_text('record_metadata', 'partition') }} as bigint) as kafka_partition,
        cast({{ record_text('record_metadata', 'offset') }} as bigint)    as kafka_offset

    from {{ source('raw', 'raw_subscription_events') }}

),

typed as (

    select
        event_id,
        event_type,
        created,
        -- An invoice event is about a subscription and is not one, so the key
        -- comes from a different field. Anything with neither reaches no
        -- subscription at all and is dropped below.
        case
            when object_kind = 'subscription' then nullif(object_id, '')
            else nullif(object_subscription, '')
        end as subscription_id,
        nullif(object_id, '')   as object_id,
        nullif(customer_id, '') as customer_id,
        nullif(status, '')      as status,
        nullif(plan, '')        as plan,
        cast(quantity_text as bigint)     as quantity,
        cast(period_start_text as bigint) as current_period_start,
        cast(period_end_text as bigint)   as current_period_end,
        case
            when cancel_text is null then null
            when lower(cancel_text) = 'true' then true
            else false
        end as cancel_at_period_end,
        kafka_partition,
        kafka_offset
    from landed

)

select *
from typed
qualify row_number() over (
    partition by event_id
    order by kafka_partition, kafka_offset
) = 1
