"""The submission deadline, held by the database clock.

The app checks the deadline first so it can answer 409 with a message. This
trigger is the backstop for every other path: the admin, a shell, a bug. After
submissions_close a project can't be created, deleted or have its content
changed. Organizer bookkeeping (duplicate_of) stays writable. The fixture
importer is the one bypass: it sets ballotbench.import for its own
transaction and writes an audit row saying so."""
from django.db import migrations

SQL = r"""
CREATE FUNCTION portal_project_deadline() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
  closes timestamptz;
BEGIN
  IF current_setting('ballotbench.import', true) = 'on' THEN
    RETURN COALESCE(NEW, OLD);
  END IF;
  SELECT submissions_close INTO closes FROM portal_event
   WHERE id = COALESCE(NEW.event_id, OLD.event_id);
  IF now() < closes THEN
    RETURN COALESCE(NEW, OLD);
  END IF;
  IF TG_OP = 'UPDATE' AND
     (NEW.event_id, NEW.team_id, NEW.track_id, NEW.title, NEW.tagline, NEW.summary,
      NEW.description, NEW.repo_url, NEW.demo_url, NEW.tags, NEW.status, NEW.submitted_at)
     IS NOT DISTINCT FROM
     (OLD.event_id, OLD.team_id, OLD.track_id, OLD.title, OLD.tagline, OLD.summary,
      OLD.description, OLD.repo_url, OLD.demo_url, OLD.tags, OLD.status, OLD.submitted_at) THEN
    RETURN NEW;
  END IF;
  RAISE EXCEPTION 'submissions closed at %', closes USING ERRCODE = 'BB409';
END $$;

CREATE TRIGGER project_deadline
  BEFORE INSERT OR UPDATE OR DELETE ON portal_project
  FOR EACH ROW EXECUTE FUNCTION portal_project_deadline();
"""

REVERSE = """
DROP TRIGGER project_deadline ON portal_project;
DROP FUNCTION portal_project_deadline();
"""


class Migration(migrations.Migration):
    dependencies = [("portal", "0001_initial")]
    operations = [migrations.RunSQL(SQL, REVERSE)]
