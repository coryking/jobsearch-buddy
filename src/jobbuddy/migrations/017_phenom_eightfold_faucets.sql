-- Category faucets for 4 Phenom tenants and 1 Eightfold tenant.
-- Each UPDATE uses jsonb merge (||) or jsonb_set to add filter keys
-- without clobbering existing config keys (locale, country, careers_url, etc.).

-- bae-systems (Phenom): tech/engineering + trades/paint bucket
UPDATE companies SET config = config || jsonb_build_object(
  'selected_fields', jsonb_build_object(
    'category', jsonb_build_array(
      'Engineering & Technology',
      'Project & Program Management',
      'Intelligence',
      'Manufacturing & Maintenance'
    )
  )
) WHERE slug = 'bae-systems';

-- rtx (Phenom): tech/engineering + trades/paint bucket
UPDATE companies SET config = config || jsonb_build_object(
  'selected_fields', jsonb_build_object(
    'category', jsonb_build_array(
      'Engineering',
      'Digital Technology',
      'Operations'
    )
  )
) WHERE slug = 'rtx';

-- us-bank (Phenom): already has selected_fields.country; add category
-- using jsonb_set to merge into the existing selected_fields object
UPDATE companies SET config = jsonb_set(
  config,
  '{selected_fields,category}',
  '["Technology & Digital"]'::jsonb
) WHERE slug = 'us-bank';

-- ge-aerospace (Phenom): tech/engineering + trades/paint bucket
UPDATE companies SET config = config || jsonb_build_object(
  'selected_fields', jsonb_build_object(
    'category', jsonb_build_array(
      'Engineering / Technology',
      'Digital Technology / IT',
      'Manufacturing & Logistics'
    )
  )
) WHERE slug = 'ge-aerospace';

-- northropgrumman (Eightfold): tech + manufacturing bucket
-- filter_department passed as repeated query params by httpx QueryParams
UPDATE companies SET config = config || jsonb_build_object(
  'default_filters', jsonb_build_object(
    'filter_department', jsonb_build_array(
      'Software Engineering',
      'Information Technology',
      'Data & Analytics',
      'Product & Design',
      'Manufacturing'
    )
  )
) WHERE slug = 'northropgrumman';
