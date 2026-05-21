-- Mistral AI moved from Ashby to Lever.
-- Old: api.ashbyhq.com/posting-api/job-board/mistral (404)
-- New: jobs.lever.co/mistral (verified active)
UPDATE companies SET ats = 'lever', board = 'mistral' WHERE slug = 'mistral';

-- Thumbtack moved from Greenhouse to Ashby.
-- Old: boards-api.greenhouse.io/v1/boards/thumbtack/jobs (404)
-- New: jobs.ashbyhq.com/thumbtack (verified active, 7 live jobs confirmed)
UPDATE companies SET ats = 'ashby', board = 'thumbtack' WHERE slug = 'thumbtack';
