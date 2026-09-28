"""The audit log can't be edited, and if someone with superuser rights edits
it anyway, verify_audit says which row."""
import io

import pytest
from django.core.management import call_command
from django.db import DatabaseError, connection, transaction

from portal import audit
from portal.models import AuditLog

pytestmark = pytest.mark.django_db


def verify():
    out, err = io.StringIO(), io.StringIO()
    try:
        call_command("verify_audit", stdout=out, stderr=err)
    except SystemExit as exc:
        return exc.code, err.getvalue()
    return 0, out.getvalue()


def test_rows_are_chained():
    audit.record("test.one", after={"n": 1})
    audit.record("test.two", after={"n": 2})
    a, b = AuditLog.objects.order_by("-seq")[:2][::-1]
    assert b.seq == a.seq + 1 and b.prev_hash == a.row_hash and len(b.row_hash) == 64
    assert verify()[0] == 0


def test_update_delete_and_truncate_are_refused():
    audit.record("test.keep")
    for sql in ("UPDATE portal_auditlog SET action = 'x'", "DELETE FROM portal_auditlog", "TRUNCATE portal_auditlog"):
        with pytest.raises(DatabaseError, match="append only"), transaction.atomic(), connection.cursor() as cur:
            cur.execute(sql)


@pytest.mark.parametrize("tamper, says", [
    ("UPDATE portal_auditlog SET after = '{\"n\": 99}' WHERE seq = %(seq)s", "edited"),
    ("DELETE FROM portal_auditlog WHERE seq = %(seq)s", "missing"),
])
def test_tampering_by_a_superuser_is_detected(tamper, says):
    audit.record("test.a", after={"n": 1})
    row = audit.record("test.b", after={"n": 2})
    audit.record("test.c", after={"n": 3})
    row.refresh_from_db()
    with connection.cursor() as cur:
        cur.execute("ALTER TABLE portal_auditlog DISABLE TRIGGER audit_readonly")
        cur.execute(tamper, {"seq": row.seq})
        cur.execute("ALTER TABLE portal_auditlog ENABLE TRIGGER audit_readonly")
    code, message = verify()
    assert code == 1 and says in message and str(row.seq) in message


def test_the_seed_import_left_an_audit_row_saying_it_bypassed_the_deadline():
    row = AuditLog.objects.filter(action="event.import").first()
    assert row.after["deadline_bypass"] is True
