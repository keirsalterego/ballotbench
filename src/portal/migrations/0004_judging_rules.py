"""Judging invariants, in the database.

- An assignment's judge holds a judge membership in the event, the project is
  in the same event, and the judge is not on the project's team.
- Nobody both judges and competes in one event: joining a team is refused to
  a judge of that event, and a judge membership is refused to a team member.
- A score belongs to a criterion of the review's event and lies in its range.
- Once any score exists in an event, its rubric is frozen: no new criteria,
  no changes to weights or ranges. Weights tuned after reading the scores are
  a way to pick a winner."""
from django.db import migrations

SQL = r"""
CREATE FUNCTION portal_assignment_rules() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM portal_project WHERE id = NEW.project_id AND event_id = NEW.event_id) THEN
    RAISE EXCEPTION 'project % is not in event %', NEW.project_id, NEW.event_id USING ERRCODE = 'BB422';
  END IF;
  IF NOT EXISTS (SELECT 1 FROM portal_membership
                  WHERE user_id = NEW.judge_id AND event_id = NEW.event_id AND role = 'judge') THEN
    RAISE EXCEPTION 'user % does not judge event %', NEW.judge_id, NEW.event_id USING ERRCODE = 'BB422';
  END IF;
  IF EXISTS (SELECT 1 FROM portal_project p JOIN portal_teammember m ON m.team_id = p.team_id
              WHERE p.id = NEW.project_id AND m.user_id = NEW.judge_id) THEN
    RAISE EXCEPTION 'judge % is on the team of project %', NEW.judge_id, NEW.project_id USING ERRCODE = 'BB423';
  END IF;
  RETURN NEW;
END $$;

CREATE TRIGGER assignment_rules
  BEFORE INSERT OR UPDATE OF event_id, judge_id, project_id ON portal_judgeassignment
  FOR EACH ROW EXECUTE FUNCTION portal_assignment_rules();

CREATE FUNCTION portal_no_judge_on_team() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF EXISTS (SELECT 1 FROM portal_membership
              WHERE user_id = NEW.user_id AND event_id = NEW.event_id AND role = 'judge') THEN
    RAISE EXCEPTION 'user % judges this event and cannot join a team in it', NEW.user_id USING ERRCODE = 'BB423';
  END IF;
  RETURN NEW;
END $$;

-- Named to sort after team_member_rules, which fills in event_id first.
CREATE TRIGGER team_member_zz_not_judge
  BEFORE INSERT OR UPDATE ON portal_teammember
  FOR EACH ROW EXECUTE FUNCTION portal_no_judge_on_team();

CREATE FUNCTION portal_judge_not_member() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF NEW.role = 'judge' AND EXISTS (SELECT 1 FROM portal_teammember
                                     WHERE user_id = NEW.user_id AND event_id = NEW.event_id) THEN
    RAISE EXCEPTION 'user % is on a team in this event and cannot judge it', NEW.user_id USING ERRCODE = 'BB423';
  END IF;
  RETURN NEW;
END $$;

CREATE TRIGGER membership_judge_not_member
  BEFORE INSERT OR UPDATE ON portal_membership
  FOR EACH ROW EXECUTE FUNCTION portal_judge_not_member();

CREATE FUNCTION portal_score_rules() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
  c portal_rubriccriterion%ROWTYPE;
  ev bigint;
BEGIN
  SELECT a.event_id INTO ev FROM portal_review r JOIN portal_judgeassignment a ON a.id = r.assignment_id
   WHERE r.id = NEW.review_id;
  SELECT * INTO c FROM portal_rubriccriterion WHERE id = NEW.criterion_id;
  IF c.event_id IS DISTINCT FROM ev THEN
    RAISE EXCEPTION 'criterion % is not in the review''s event', NEW.criterion_id USING ERRCODE = 'BB422';
  END IF;
  IF NEW.value < c.min_value OR NEW.value > c.max_value THEN
    RAISE EXCEPTION '% must be between % and %', c.key, c.min_value, c.max_value USING ERRCODE = 'BB422';
  END IF;
  RETURN NEW;
END $$;

CREATE TRIGGER score_rules
  BEFORE INSERT OR UPDATE ON portal_score
  FOR EACH ROW EXECUTE FUNCTION portal_score_rules();

CREATE FUNCTION portal_rubric_frozen() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF TG_OP = 'UPDATE' AND (NEW.event_id, NEW.key, NEW.weight, NEW.min_value, NEW.max_value)
                          IS NOT DISTINCT FROM (OLD.event_id, OLD.key, OLD.weight, OLD.min_value, OLD.max_value) THEN
    RETURN NEW;
  END IF;
  IF EXISTS (SELECT 1 FROM portal_score s JOIN portal_rubriccriterion c ON c.id = s.criterion_id
              WHERE c.event_id = COALESCE(NEW.event_id, OLD.event_id)) THEN
    RAISE EXCEPTION 'the rubric is frozen: this event already has scores' USING ERRCODE = 'BB409';
  END IF;
  RETURN COALESCE(NEW, OLD);
END $$;

CREATE TRIGGER rubric_frozen
  BEFORE INSERT OR UPDATE OR DELETE ON portal_rubriccriterion
  FOR EACH ROW EXECUTE FUNCTION portal_rubric_frozen();
"""

REVERSE = """
DROP TRIGGER rubric_frozen ON portal_rubriccriterion; DROP FUNCTION portal_rubric_frozen();
DROP TRIGGER score_rules ON portal_score; DROP FUNCTION portal_score_rules();
DROP TRIGGER membership_judge_not_member ON portal_membership; DROP FUNCTION portal_judge_not_member();
DROP TRIGGER team_member_zz_not_judge ON portal_teammember; DROP FUNCTION portal_no_judge_on_team();
DROP TRIGGER assignment_rules ON portal_judgeassignment; DROP FUNCTION portal_assignment_rules();
"""


class Migration(migrations.Migration):
    dependencies = [("portal", "0003_team_rules")]
    operations = [migrations.RunSQL(SQL, REVERSE)]
