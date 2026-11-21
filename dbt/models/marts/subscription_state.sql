-- The state fold, in SQL.
--
-- subevents/state.py already does this in Python. This is the second reading
-- and the point of it is that the two disagree out loud. The Python fold runs
-- where the handler runs and this one runs where the data lands, so a platform
-- that only has the first one has nothing running in the warehouse.
--
-- A subscription event carries the object as it is after the change, so every
-- field is taken rather than adjusted. That is what makes a replay harmless
-- and it is also what makes the fold expressible as a last value per field.
--
-- max_by with a sentinel ordering column is how last non null is spelled here.
-- A filtered aggregate would be shorter and the filter clause is not portable
-- to the warehouse this ships against.

with ordered as (

    select * from {{ ref('int_event_order') }}

),

subscription_fields as (

    select
        subscription_id,
        max_by(customer_id, case when customer_id is null then -1 else apply_order end) as customer_id,
        max_by(status, case when status is null then -1 else apply_order end) as status,
        max_by(plan, case when plan is null then -1 else apply_order end) as plan,
        max_by(quantity, case when quantity is null then -1 else apply_order end) as quantity,
        max_by(
            current_period_start,
            case when current_period_start is null then -1 else apply_order end
        ) as current_period_start,
        max_by(
            current_period_end,
            case when current_period_end is null then -1 else apply_order end
        ) as current_period_end,
        max_by(
            cancel_at_period_end,
            case when cancel_at_period_end is null then -1 else apply_order end
        ) as cancel_at_period_end
    from ordered
    where is_invoice = false
    group by 1

),

invoice_fields as (

    select
        subscription_id,
        sum(case when event_type = 'invoice.payment_succeeded' then 1 else 0 end) as invoices_paid,
        sum(case when event_type = 'invoice.payment_failed' then 1 else 0 end) as invoices_failed,
        max_by(object_id, case when object_id is null then -1 else apply_order end) as last_invoice_id,
        max_by(status, case when status is null then -1 else apply_order end) as last_invoice_status
    from ordered
    where is_invoice = true
    group by 1

),

totals as (

    select
        subscription_id,
        count(*)   as events_applied,
        min(created) as first_event_at,
        max(created) as last_event_at
    from ordered
    group by 1

)

select
    totals.subscription_id,
    coalesce(subscription_fields.customer_id, '')                as customer_id,
    coalesce(subscription_fields.status, '')                     as status,
    coalesce(subscription_fields.plan, '')                       as plan,
    coalesce(subscription_fields.quantity, 0)                    as quantity,
    coalesce(subscription_fields.current_period_start, 0)        as current_period_start,
    coalesce(subscription_fields.current_period_end, 0)          as current_period_end,
    coalesce(subscription_fields.cancel_at_period_end, false)    as cancel_at_period_end,
    totals.first_event_at,
    totals.last_event_at,
    totals.events_applied,
    coalesce(invoice_fields.invoices_paid, 0)                    as invoices_paid,
    coalesce(invoice_fields.invoices_failed, 0)                  as invoices_failed,
    coalesce(invoice_fields.last_invoice_id, '')                 as last_invoice_id,
    coalesce(invoice_fields.last_invoice_status, '')             as last_invoice_status,
    coalesce(ambiguity.ambiguous_seconds, 0)                     as ambiguous_seconds
from totals
left join subscription_fields using (subscription_id)
left join invoice_fields using (subscription_id)
left join {{ ref('int_ambiguous_seconds') }} as ambiguity using (subscription_id)
