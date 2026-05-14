-- Enable the `uber` company (row already exists with ats=NULL, config='{}').
--
-- Uber publishes a large unsegmented catalog via a single global board, so
-- registering it without a faucet would pull ~1k listings per sync. Apply
-- an engineering-shaped department filter at the fetcher boundary — the
-- Uber API ORs within `department` and ANDs across keys. `team`/`location`
-- stay empty: jsb is an evidence provider, the LLM does the ranking, so
-- over-narrowing here would hide candidates the ranker could still use.
--
-- The `board` value is documented as ignored by UberFetcher (single global
-- board); we set it to 'uber' as a stable placeholder.
--
-- Additive jsonb merge (`||`) — re-running this migration is a no-op
-- because the same key/value pair just overwrites itself.

UPDATE companies SET
  ats = 'uber',
  board = 'uber',
  config = config || jsonb_build_object(
    'selected_fields', jsonb_build_object(
      'department', jsonb_build_array('Engineering', 'Data Science', 'Product')
    )
  )
WHERE slug = 'uber';
