"""The audit log is append only and hash chained.

A BEFORE INSERT trigger takes a transaction-scoped advisory lock, gives the
row the next chain position (seq), and sets
    row_hash = sha256(prev_hash | seq | ts | actor | event | action | object | before | after | ip)
so editing or deleting any row breaks every hash after it. UPDATE, DELETE and
TRUNCATE are refused outright. `manage.py verify_audit` recomputes the chain.

What this doesn't stop: a database superuser can disable the trigger and
rewrite the whole chain. Publishing the latest hash somewhere else (a results
page, a webhook) makes that detectable; THREAT-MODEL.md says so."""
from django.db import migrations

SQL = r"""
CREATE FUNCTION portal_audit_hash(r portal_auditlog) RETURNS text LANGUAGE sql IMMUTABLE AS $$
  SELECT encode(sha256(convert_to(concat_ws('|',
    r.prev_hash, r.seq,
    to_char(r.ts AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US'),
    coalesce(r.actor_id::text, ''), coalesce(r.event_id::text, ''),
    r.action, r.object_type, r.object_id,
    coalesce(r.before::text, ''), coalesce(r.after::text, ''),
    coalesce(host(r.ip), '')), 'UTF8')), 'hex')
$$;

CREATE FUNCTION portal_audit_append() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
  last portal_auditlog%ROWTYPE;
BEGIN
  PERFORM pg_advisory_xact_lock(hashtext('portal_auditlog'));
  SELECT * INTO last FROM portal_auditlog ORDER BY seq DESC LIMIT 1;
  NEW.seq := coalesce(last.seq, 0) + 1;
  NEW.prev_hash := coalesce(last.row_hash, '');
  NEW.ts := clock_timestamp();
  NEW.row_hash := portal_audit_hash(NEW);
  RETURN NEW;
END $$;

CREATE TRIGGER audit_append
  BEFORE INSERT ON portal_auditlog
  FOR EACH ROW EXECUTE FUNCTION portal_audit_append();

CREATE FUNCTION portal_audit_readonly() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  RAISE EXCEPTION 'the audit log is append only' USING ERRCODE = 'BB403';
END $$;

CREATE TRIGGER audit_readonly
  BEFORE UPDATE OR DELETE ON portal_auditlog
  FOR EACH ROW EXECUTE FUNCTION portal_audit_readonly();

CREATE TRIGGER audit_no_truncate
  BEFORE TRUNCATE ON portal_auditlog
  FOR EACH STATEMENT EXECUTE FUNCTION portal_audit_readonly();
"""

REVERSE = """
DROP TRIGGER audit_no_truncate ON portal_auditlog;
DROP TRIGGER audit_readonly ON portal_auditlog;
DROP FUNCTION portal_audit_readonly();
DROP TRIGGER audit_append ON portal_auditlog;
DROP FUNCTION portal_audit_append();
DROP FUNCTION portal_audit_hash(portal_auditlog);
"""


class Migration(migrations.Migration):
    dependencies = [("portal", "0004_judging_rules")]
    operations = [migrations.RunSQL(SQL, REVERSE)]
