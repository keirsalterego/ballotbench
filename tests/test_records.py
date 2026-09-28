"""Signed records and certificates: a record checks out with only the public
key, any change to it doesn't, and nobody gets someone else's."""
import json

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from portal import signing
from portal.models import AuditLog, User

from .conftest import EMAILS, EVENT

pytestmark = pytest.mark.django_db


def own_record(api, role="judge_a"):
    response = api(role).get(f"/api/judge/record?event={EVENT}")
    assert response.status_code == 200
    return response.json()


def test_sign_and_verify_roundtrip():
    doc = signing.sign({"hello": "wörld", "n": 3})
    valid, reason, record = signing.verify(json.dumps(doc))
    assert valid and record["hello"] == "wörld"
    assert record["key_id"] == signing.key_id(signing.public_key())


def test_judge_record_verifies_and_holds_no_scores(api):
    doc = own_record(api)
    r = doc["record"]
    assert r["role"] == "judge" and r["person"]["email"] == EMAILS["judge_a"] and r["event"]["slug"] == EVENT
    assert r["projects_assigned"] >= 3 and r["reviews_submitted"] >= 3 and r["first_review_at"]
    assert not {"scores", "criteria", "comment"} & set(json.dumps(r).replace('"', " ").split())
    response = api().post("/api/verify", json.dumps(doc), content_type="application/json")
    assert response.json()["valid"] is True
    assert AuditLog.objects.filter(action="record.issue", after__subject=EMAILS["judge_a"]).exists()


@pytest.mark.parametrize("change", [
    lambda d: d["record"].update(reviews_submitted=d["record"]["reviews_submitted"] + 1),
    lambda d: d["record"]["person"].update(name="Someone Else"),
    lambda d: d["record"].update(extra=True),
    lambda d: d.update(signature=d["signature"][:-2] + ("AA" if d["signature"][-2:] != "AA" else "BB")),
])
def test_any_change_breaks_the_signature(api, change):
    doc = own_record(api)
    change(doc)
    body = api().post("/api/verify", json.dumps(doc), content_type="application/json").json()
    assert body["valid"] is False and "signature" in body["reason"]


def test_wrong_key_fails():
    other = Ed25519PrivateKey.generate()
    doc = signing.sign({"n": 1}, key=other)
    valid, reason, _ = signing.verify(json.dumps(doc))
    assert not valid and "not this portal's key" in reason
    # Claiming our key id doesn't help: the signature still isn't ours.
    doc["record"]["key_id"] = signing.key_id(signing.public_key())
    doc["signature"] = signing.b64url(other.sign(signing.canonical(doc["record"])))
    assert not signing.verify(json.dumps(doc))[0]
    # And our record doesn't check out against someone else's key.
    assert not signing.verify(json.dumps(signing.sign({"n": 1})), other.public_key())[0]


@pytest.mark.parametrize("body", ["", "not json", "[]", "null", "{}", '{"record": 1, "signature": "x"}',
                                  '{"record": {}, "signature": 5}', '{"record": {}, "signature": "***"}',
                                  "[" * 100_000, '{"record": {"key_id": "\\ud800"}, "signature": ""}',
                                  '{"record": {"n": NaN}, "signature": ""}', '{"record": {"n": ' + "9" * 5000 + "}}",
                                  "x" * 30_000, "\xff\xfe"])
def test_malformed_input_is_invalid_not_an_error(api, web, body):
    response = api().post("/api/verify", body, content_type="application/json")
    assert response.status_code == 200 and response.json()["valid"] is False
    page = web().post("/verify", {"record": body})
    assert page.status_code == 200


def test_verify_page_shows_the_result(api, web):
    doc = own_record(api)
    page = web().post("/verify", {"record": json.dumps(doc)}).content.decode()
    assert "Valid" in page and "Diego" in page
    doc["record"]["reviews_submitted"] = 99
    page = web().get("/verify", {"record": json.dumps(doc)}).content.decode()
    assert "Not valid" in page


def test_public_key_is_published():
    from django.test import Client
    info = Client().get("/.well-known/ballotbench-signing-key").json()
    assert info["algorithm"] == "Ed25519" and "BEGIN PUBLIC KEY" in info["pem"]
    assert info["key_id"] == signing.key_id(signing.public_key()) and len(info["key_id"]) == 16


def test_judge_cannot_fetch_another_judges_record(api):
    for named in ("jdg_24", EMAILS["judge_a"], str(User.objects.get(email=EMAILS["judge_a"]).pk), "nobody"):
        assert api("judge_b").get(f"/api/judge/record?event={EVENT}&judge={named}").status_code == 403


def test_record_access_rules(api):
    assert api().get(f"/api/judge/record?event={EVENT}").status_code == 401
    assert api("participant").get(f"/api/judge/record?event={EVENT}").status_code == 403
    assert api("judge_a").get("/api/judge/record").status_code == 400
    assert api("judge_a").get("/api/judge/record?event=no-such-event").status_code == 404
    assert api("judge_a").get(f"/api/judge/record?event={EVENT}&judge=jdg_24").status_code == 200
    doc = api("organizer").get(f"/api/judge/record?event={EVENT}&judge=jdg_24").json()
    assert doc["record"]["person"]["email"] == EMAILS["judge_a"]
    assert api("organizer").get(f"/api/judge/record?event={EVENT}&judge=jdg_99").status_code == 404


def test_participant_record(api):
    doc = api("participant").get(f"/api/participant/record?event={EVENT}").json()
    r = doc["record"]
    assert r["role"] == "participant" and r["team"] == "NorthKiln"
    assert r["projects"] and r["projects"][0]["submitted_at"].endswith("Z")
    assert signing.verify(json.dumps(doc))[0]
    assert api("judge_a").get(f"/api/participant/record?event={EVENT}").status_code == 403


def test_certificate_is_for_its_owner_and_organizers(web):
    url = f"/events/{EVENT}/certificate"
    assert web().get(url).status_code == 302                     # to the login page
    page = web("judge_a").get(url)
    assert page.status_code == 200 and b"Certificate of judging" in page.content
    assert b"Certificate of participation" in web("participant").get(url).content
    assert web("judge_b").get(f"{url}?person={EMAILS['judge_a']}").status_code == 404
    assert web("participant").get(f"{url}?person={EMAILS['judge_a']}").status_code == 404
    assert web("organizer").get(url).status_code == 404          # organizes, didn't take part
    page = web("organizer").get(f"{url}?person={EMAILS['judge_a']}")
    assert page.status_code == 200 and EMAILS["judge_a"].encode() in page.content


def test_certificate_embeds_a_valid_record(web):
    page = web("judge_a").get(f"/events/{EVENT}/certificate")
    compact = page.context["compact"]
    assert signing.verify(compact)[0]
    assert "/verify?record=" in page.context["verify_url"]
