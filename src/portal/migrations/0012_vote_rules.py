"""Public voting rules, in the database.

Before any vote is written or removed: the voter row is locked (so two
requests from one voter queue instead of both spending the same credits),
the event's voting window must be open on the database clock, the voter must
be confirmed and not voided, the project must be a submitted, non-duplicate
project of the same event and not the voter's own team's, and the voter's
total cost, the sum of votes squared, must fit the event's budget.
The maintenance flag (import, delete_event) bypasses it, as for projects."""
from django.db import migrations

SQL = r"""
CREATE FUNCTION portal_vote_rules() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
  v portal_voter%ROWTYPE;
  e portal_event%ROWTYPE;
  spent int;
BEGIN
  IF current_setting('ballotbench.import', true) = 'on' THEN
    RETURN COALESCE(NEW, OLD);
  END IF;
  SELECT * INTO v FROM portal_voter WHERE id = COALESCE(NEW.voter_id, OLD.voter_id) FOR UPDATE;
  SELECT * INTO e FROM portal_event WHERE id = v.event_id;
  IF e.voting_mode = 'off' OR e.voting_open IS NULL OR e.voting_close IS NULL
     OR now() < e.voting_open OR now() >= e.voting_close THEN
    RAISE EXCEPTION 'voting for % is not open', e.name USING ERRCODE = 'BB409';
  END IF;
  IF v.voided_at IS NOT NULL THEN
    RAISE EXCEPTION 'this ballot was voided by the organizers' USING ERRCODE = 'BB409';
  END IF;
  IF v.confirmed_at IS NULL THEN
    RAISE EXCEPTION 'confirm your email address before voting' USING ERRCODE = 'BB409';
  END IF;
  IF TG_OP = 'DELETE' THEN
    RETURN OLD;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM portal_project WHERE id = NEW.project_id AND event_id = e.id
                  AND status = 'submitted' AND duplicate_of_id IS NULL) THEN
    RAISE EXCEPTION 'project % is not on this ballot', NEW.project_id USING ERRCODE = 'BB422';
  END IF;
  IF v.user_id IS NOT NULL AND EXISTS (
       SELECT 1 FROM portal_project p JOIN portal_teammember m ON m.team_id = p.team_id
        WHERE p.id = NEW.project_id AND m.user_id = v.user_id) THEN
    RAISE EXCEPTION 'you can''t vote for your own team' USING ERRCODE = 'BB423';
  END IF;
  SELECT COALESCE(SUM(votes * votes), 0) INTO spent FROM portal_vote
   WHERE voter_id = NEW.voter_id AND id IS DISTINCT FROM NEW.id;
  IF spent + NEW.votes * NEW.votes > e.vote_credits THEN
    RAISE EXCEPTION 'that costs % credits and you have % left', NEW.votes * NEW.votes, e.vote_credits - spent
      USING ERRCODE = 'BB409';
  END IF;
  RETURN NEW;
END $$;

CREATE TRIGGER vote_rules
  BEFORE INSERT OR UPDATE OR DELETE ON portal_vote
  FOR EACH ROW EXECUTE FUNCTION portal_vote_rules();
"""

REVERSE = "DROP TRIGGER vote_rules ON portal_vote; DROP FUNCTION portal_vote_rules();"


class Migration(migrations.Migration):
    dependencies = [("portal", "0011_public_voting")]
    operations = [migrations.RunSQL(SQL, REVERSE)]
