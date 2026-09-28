"""Ed25519 signatures for participation records.

A record is a small JSON object; what gets signed is its canonical form
(sorted keys, no spaces, UTF-8), so anyone can check a record with nothing
but the public key at /.well-known/ballotbench-signing-key: no database, no
account, no network. The private key lives in one file, made on first use
and readable only by the portal's user."""
import base64
import functools
import hashlib
import json
import os
from pathlib import Path

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from django.conf import settings

MAX_LENGTH = 20_000     # a real record is under 1 kB
SHAPE = 'expected {"record": {...}, "signature": "..."}'


def canonical(obj):
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()


def b64url(data):
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _unb64url(text):
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _create(path):
    """Write a new key without ever replacing one: another worker may be
    doing the same thing at the same moment, and the first link wins."""
    path.parent.mkdir(parents=True, exist_ok=True)
    pem = Ed25519PrivateKey.generate().private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(pem)
    try:
        os.link(tmp, path)
    except FileExistsError:
        pass
    finally:
        os.unlink(tmp)


@functools.cache
def private_key():
    path = Path(settings.SIGNING_KEY_FILE)
    if not path.exists():
        _create(path)
    key = serialization.load_pem_private_key(path.read_bytes(), password=None)
    if not isinstance(key, Ed25519PrivateKey):
        raise RuntimeError(f"{path} is not an Ed25519 private key")
    return key


def public_key():
    return private_key().public_key()


def raw(public):
    return public.public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)


def key_id(public):
    """The first 16 hex digits of the SHA-256 of the raw public key."""
    return hashlib.sha256(raw(public)).hexdigest()[:16]


def public_key_info(public=None):
    public = public or public_key()
    pem = public.public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
    return {"algorithm": "Ed25519", "key_id": key_id(public), "public_key": b64url(raw(public)),
            "pem": pem.decode()}


def sign(record, key=None):
    """{"record": record + key_id, "signature": base64url} for `record`."""
    key = key or private_key()
    record = {**record, "key_id": key_id(key.public_key())}
    return {"record": record, "signature": b64url(key.sign(canonical(record)))}


def verify(text, public=None):
    """Check a pasted record. Returns (valid, reason, record or None). Never
    raises on bad input: anything malformed is simply not valid."""
    public = public or public_key()
    if not isinstance(text, str) or len(text) > MAX_LENGTH:
        return False, "That's too long to be a record.", None
    try:
        doc = json.loads(text)
    except (ValueError, RecursionError):
        return False, "That isn't JSON. Paste the whole record, braces included.", None
    if not (isinstance(doc, dict) and isinstance(doc.get("record"), dict) and isinstance(doc.get("signature"), str)):
        return False, f"That isn't a record: {SHAPE}.", None
    record, ours = doc["record"], key_id(public)
    try:
        body = canonical(record)
    except ValueError:      # a lone surrogate, say: it can't have been signed, and can't be echoed back
        return False, f"That isn't a record: {SHAPE}.", None
    if record.get("key_id") != ours:
        return False, f"It names signing key {str(record.get('key_id'))[:40]!r}, not this portal's key {ours}.", record
    try:
        public.verify(_unb64url(doc["signature"]), body)
    except (ValueError, InvalidSignature):
        return False, ("The signature doesn't match: the record was changed after it was signed, "
                       "or someone else signed it."), record
    return True, "Valid: this portal signed exactly this record.", record
