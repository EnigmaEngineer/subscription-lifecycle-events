-- dbt appends a model's configured schema to the profile's by default, so a
-- profile schema of ANALYTICS and a model schema of STAGING resolve to
-- ANALYTICS_STAGING. Nothing complains about that. The warehouse configuration
-- creates no such schema and the transforming role holds no CREATE SCHEMA, so
-- the first real run would fail on a name that appears in no file.
--
-- This override makes the configured schema the whole name. Every model here
-- sets one, so the profile schema is only ever the fallback.
{% macro generate_schema_name(custom_schema_name, node) -%}
    {%- if custom_schema_name is none -%}
        {{ target.schema }}
    {%- else -%}
        {{ custom_schema_name | trim }}
    {%- endif -%}
{%- endmacro %}
