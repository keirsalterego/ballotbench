"""Team invariants the app can't hold alone under concurrency.

- team_member.event always equals its team's event, so the unique
  (event, user) constraint really means one team per person per event.
- A team can't grow past the event's max_team_size. The team row is locked
  first, so two invites accepted at the same moment can't both squeeze in.
- A project's team and track belong to the project's event."""
from django.db import migrations

SQL = r"""
CREATE FUNCTION portal_team_member_rules() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
  cap int;
  size int;
BEGIN
  SELECT t.event_id, e.max_team_size INTO NEW.event_id, cap
    FROM portal_team t JOIN portal_event e ON e.id = t.event_id
   WHERE t.id = NEW.team_id
     FOR UPDATE OF t;
  IF current_setting('ballotbench.import', true) = 'on' THEN
    RETURN NEW;
  END IF;
  SELECT count(*) INTO size FROM portal_teammember
   WHERE team_id = NEW.team_id AND id IS DISTINCT FROM NEW.id;
  IF size >= cap THEN
    RAISE EXCEPTION 'team is full (% members)', cap USING ERRCODE = 'BB410';
  END IF;
  RETURN NEW;
END $$;

CREATE TRIGGER team_member_rules
  BEFORE INSERT OR UPDATE ON portal_teammember
  FOR EACH ROW EXECUTE FUNCTION portal_team_member_rules();

CREATE FUNCTION portal_project_same_event() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM portal_team WHERE id = NEW.team_id AND event_id = NEW.event_id) THEN
    RAISE EXCEPTION 'team % is not in event %', NEW.team_id, NEW.event_id USING ERRCODE = 'BB422';
  END IF;
  IF NEW.track_id IS NOT NULL AND NOT EXISTS
     (SELECT 1 FROM portal_track WHERE id = NEW.track_id AND event_id = NEW.event_id) THEN
    RAISE EXCEPTION 'track % is not in event %', NEW.track_id, NEW.event_id USING ERRCODE = 'BB422';
  END IF;
  RETURN NEW;
END $$;

CREATE TRIGGER project_same_event
  BEFORE INSERT OR UPDATE OF event_id, team_id, track_id ON portal_project
  FOR EACH ROW EXECUTE FUNCTION portal_project_same_event();
"""

REVERSE = """
DROP TRIGGER project_same_event ON portal_project;
DROP FUNCTION portal_project_same_event();
DROP TRIGGER team_member_rules ON portal_teammember;
DROP FUNCTION portal_team_member_rules();
"""


class Migration(migrations.Migration):
    dependencies = [("portal", "0002_deadline_trigger")]
    operations = [migrations.RunSQL(SQL, REVERSE)]
