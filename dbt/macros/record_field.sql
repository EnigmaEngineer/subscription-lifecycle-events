-- The one place in this project that knows which warehouse it is running on.
-- Every model below the staging layer is plain SQL on typed columns, so the
-- dialect is confined to this file rather than spread over the models.
{% macro record_text(column, path) %}
  {{ return(adapter.dispatch('record_text', 'subevents')(column, path)) }}
{% endmacro %}

{% macro default__record_text(column, path) %}
  json_extract_string({{ column }}, '$.{{ path }}')
{% endmacro %}

{% macro snowflake__record_text(column, path) %}
  {{ column }}:{{ path }}::string
{% endmacro %}
