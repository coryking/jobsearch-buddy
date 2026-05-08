-- Narrow TalentBrew fetches to relevant job categories via per-company filter config.
-- Each UPDATE sets the full config for that company (tb_host + tb_tenant_id + filter fields).

-- Pharmacy / retail jobs only
UPDATE companies
    SET config = '{"tb_host": "jobs.walgreens.com", "tb_tenant_id": 1242, "tb_search_path": "/en/search-jobs?acm=12899"}'::jsonb
    WHERE slug = 'walgreens';

-- Finance / technology / operations roles
UPDATE companies
    SET config = '{"tb_host": "jobs.citi.com", "tb_tenant_id": 287, "tb_search_path": "/en/search-jobs?acm=19627,8295232,8644128"}'::jsonb
    WHERE slug = 'citi';

-- Engineering / manufacturing categories; static pagination + keyword pass for specialty trades
UPDATE companies
    SET config = '{"tb_host": "jobs.boeing.com", "tb_tenant_id": 185, "tb_search_path": "/search-jobs/185/1?acm=11125,2630,4744,18389,2644", "tb_static_pagination": true, "tb_keyword_passes": ["paint"]}'::jsonb
    WHERE slug = 'boeing';

-- Technology / network operations categories; static pagination required
UPDATE companies
    SET config = '{"tb_host": "jobs.spectrum.com", "tb_tenant_id": 4673, "tb_search_path": "/search-jobs/4673/1?acm=26210,63667,81595,81694,68006,81729,81593,81594", "tb_static_pagination": true}'::jsonb
    WHERE slug = 'spectrum';

-- Technology / product / engineering categories
UPDATE companies
    SET config = '{"tb_host": "jobs.comcast.com", "tb_tenant_id": 45483, "tb_search_path": "/en/search-jobs?acm=8795856,8856496,8795824,8796032,8795920,8795888,8707664,8707408"}'::jsonb
    WHERE slug = 'comcast';

-- Defense electronics engineering categories + specialty coatings keyword pass
UPDATE companies
    SET config = '{"tb_host": "careers.l3harris.com", "tb_tenant_id": 4832, "tb_search_path": "/en/search-jobs?acm=61370,65926,61407", "tb_keyword_passes": ["coatings"]}'::jsonb
    WHERE slug = 'l3harris';

-- Entertainment / theme park operations categories; static pagination required
UPDATE companies
    SET config = '{"tb_host": "jobs.disneycareers.com", "tb_tenant_id": 391, "tb_search_path": "/search-jobs/391/1?acm=26715,21579,8221776,8221696", "tb_static_pagination": true}'::jsonb
    WHERE slug = 'disney';
