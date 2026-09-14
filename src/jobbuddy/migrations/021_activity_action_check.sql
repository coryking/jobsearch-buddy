ALTER TABLE activity_log
  ADD CONSTRAINT activity_action_check
  CHECK (action IN ('Application','Contact','Screen','Interview',
                    'Referral','Reach-out','Inquery'));
