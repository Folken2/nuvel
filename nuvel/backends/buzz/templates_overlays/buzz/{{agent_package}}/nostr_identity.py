"""The Nostr identity {{agent_name}} signs with.

Everything a Buzz agent publishes is a Nostr event signed with a secp256k1
key (NIP-01), so the agent needs a keypair, a BIP-340 Schnorr signature, and
the bech32 encoding NIP-19 uses for ``npub…`` / ``nsec…``. All three are
implemented here in pure Python — a few hundred lines of well-specified
arithmetic, in exchange for the agent having no crypto dependency to install,
audit, or pin.

Get the identity with :func:`load_identity`, which resolves in this order:

1. ``NOSTR_PRIVATE_KEY`` — hex or ``nsec1…``;
2. ``NOSTR_KEY_FILE`` (default ``.nostr/identity.json``) if it exists;
3. otherwise generate a fresh key and persist it to that file, mode ``0600``.

Print the public identity with::

    python -m {{agent_package}}.nostr_identity
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import time
from dataclasses import dataclass
from pathlib import Path

# ── secp256k1 ────────────────────────────────────────────────────────

_P = 2**256 - 2**32 - 977
_N = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEBAAEDCE6AF48A03BBFD25E8CD0364141
_G = (
    0x79BE667EF9DCBBAC55A06295CE870B07029BFCDB2DCE28D959F2815B16F81798,
    0x483ADA7726A3C4655DA4FBFC0E1108A8FD17B448A68554199C47D08FFB10D4B8,
)


# Points are affine ``(x, y)`` tuples; ``None`` is the point at infinity.


def _inv(a: int) -> int:
    return pow(a, _P - 2, _P)


def _point_add(p1, p2):
    if p1 is None:
        return p2
    if p2 is None:
        return p1
    x1, y1 = p1
    x2, y2 = p2
    if x1 == x2 and (y1 + y2) % _P == 0:
        return None
    if p1 == p2:
        lam = (3 * x1 * x1 * _inv(2 * y1)) % _P
    else:
        lam = ((y2 - y1) * _inv(x2 - x1)) % _P
    x3 = (lam * lam - x1 - x2) % _P
    return (x3, (lam * (x1 - x3) - y1) % _P)


def _point_mul(k: int, point=_G):
    """Scalar multiplication, double-and-add over the affine curve."""
    result = None
    addend = point
    while k:
        if k & 1:
            result = _point_add(result, addend)
        addend = _point_add(addend, addend)
        k >>= 1
    return result


def _lift_x(x: int):
    """Recover the even-y point with x-coordinate ``x`` (BIP-340 lift_x)."""
    if not 0 < x < _P:
        return None
    y_sq = (pow(x, 3, _P) + 7) % _P
    y = pow(y_sq, (_P + 1) // 4, _P)
    if pow(y, 2, _P) != y_sq:
        return None
    return (x, y if y % 2 == 0 else _P - y)


def _b32(value: int) -> bytes:
    return value.to_bytes(32, "big")


def _tagged_hash(tag: str, msg: bytes) -> bytes:
    tag_hash = hashlib.sha256(tag.encode()).digest()
    return hashlib.sha256(tag_hash + tag_hash + msg).digest()


# ── BIP-340 Schnorr ──────────────────────────────────────────────────


def generate_private_key() -> bytes:
    """A fresh 32-byte secret key in ``[1, n-1]``."""
    while True:
        key = secrets.token_bytes(32)
        if 0 < int.from_bytes(key, "big") < _N:
            return key


def public_key(private_key: bytes) -> bytes:
    """The x-only (32-byte) public key for a secret key."""
    d = int.from_bytes(private_key, "big")
    if not 0 < d < _N:
        raise ValueError("Private key out of range for secp256k1.")
    point = _point_mul(d)
    return _b32(point[0])


def sign(message: bytes, private_key: bytes, aux_rand: bytes | None = None) -> bytes:
    """BIP-340 Schnorr signature over a 32-byte message hash."""
    if len(message) != 32:
        raise ValueError("BIP-340 signs a 32-byte message hash.")
    d0 = int.from_bytes(private_key, "big")
    if not 0 < d0 < _N:
        raise ValueError("Private key out of range for secp256k1.")

    point = _point_mul(d0)
    # The x-only convention: negate the secret when P has odd y.
    d = d0 if point[1] % 2 == 0 else _N - d0

    aux = aux_rand if aux_rand is not None else secrets.token_bytes(32)
    t = _b32(d ^ int.from_bytes(_tagged_hash("BIP0340/aux", aux), "big"))
    k0 = int.from_bytes(
        _tagged_hash("BIP0340/nonce", t + _b32(point[0]) + message), "big"
    ) % _N
    if k0 == 0:  # ~2^-256; retrying with fresh aux is the spec's escape hatch
        raise RuntimeError("Nonce generation failed; retry the signature.")

    r_point = _point_mul(k0)
    k = k0 if r_point[1] % 2 == 0 else _N - k0
    e = int.from_bytes(
        _tagged_hash(
            "BIP0340/challenge", _b32(r_point[0]) + _b32(point[0]) + message
        ),
        "big",
    ) % _N
    return _b32(r_point[0]) + _b32((k + e * d) % _N)


def verify(message: bytes, pubkey: bytes, signature: bytes) -> bool:
    """Check a BIP-340 signature. Used on inbound relay events."""
    if len(message) != 32 or len(pubkey) != 32 or len(signature) != 64:
        return False
    point = _lift_x(int.from_bytes(pubkey, "big"))
    if point is None:
        return False

    r = int.from_bytes(signature[:32], "big")
    s = int.from_bytes(signature[32:], "big")
    if r >= _P or s >= _N:
        return False

    e = int.from_bytes(
        _tagged_hash("BIP0340/challenge", signature[:32] + pubkey + message), "big"
    ) % _N
    # R = sG - eP must have even y and x == r.
    r_point = _point_add(_point_mul(s), _point_mul(_N - e, point))
    if r_point is None or r_point[1] % 2 != 0 or r_point[0] != r:
        return False
    return True


# ── bech32 (NIP-19 npub / nsec) ──────────────────────────────────────

_CHARSET = "qpzry9x8gf2tvdw0s3jn54khce6mua7l"


def _polymod(values: list[int]) -> int:
    generator = [0x3B6A57B2, 0x26508E6D, 0x1EA119FA, 0x3D4233DD, 0x2A1462B3]
    chk = 1
    for value in values:
        top = chk >> 25
        chk = ((chk & 0x1FFFFFF) << 5) ^ value
        for i in range(5):
            chk ^= generator[i] if ((top >> i) & 1) else 0
    return chk


def _hrp_expand(hrp: str) -> list[int]:
    return [ord(c) >> 5 for c in hrp] + [0] + [ord(c) & 31 for c in hrp]


def _convertbits(data, from_bits: int, to_bits: int, pad: bool = True):
    acc = 0
    bits = 0
    out: list[int] = []
    maxv = (1 << to_bits) - 1
    for value in data:
        if value < 0 or (value >> from_bits):
            return None
        acc = (acc << from_bits) | value
        bits += from_bits
        while bits >= to_bits:
            bits -= to_bits
            out.append((acc >> bits) & maxv)
    if pad:
        if bits:
            out.append((acc << (to_bits - bits)) & maxv)
    elif bits >= from_bits or ((acc << (to_bits - bits)) & maxv):
        return None
    return out


def bech32_encode(hrp: str, data: bytes) -> str:
    values = _convertbits(data, 8, 5)
    checksum_input = _hrp_expand(hrp) + values + [0, 0, 0, 0, 0, 0]
    polymod = _polymod(checksum_input) ^ 1
    checksum = [(polymod >> 5 * (5 - i)) & 31 for i in range(6)]
    return hrp + "1" + "".join(_CHARSET[d] for d in values + checksum)


def bech32_decode(encoded: str) -> tuple[str, bytes]:
    """Decode a bech32 string into ``(hrp, payload)``; raises on bad input."""
    text = encoded.strip().lower()
    pos = text.rfind("1")
    if pos < 1 or pos + 7 > len(text):
        raise ValueError(f"Not a bech32 string: {encoded!r}")
    hrp, body = text[:pos], text[pos + 1:]
    try:
        values = [_CHARSET.index(c) for c in body]
    except ValueError:
        raise ValueError(f"Invalid bech32 character in {encoded!r}") from None
    if _polymod(_hrp_expand(hrp) + values) != 1:
        raise ValueError(f"Bad bech32 checksum in {encoded!r}")
    payload = _convertbits(values[:-6], 5, 8, pad=False)
    if payload is None:
        raise ValueError(f"Bad bech32 payload in {encoded!r}")
    return hrp, bytes(payload)


def to_npub(pubkey: bytes) -> str:
    return bech32_encode("npub", pubkey)


def to_nsec(private_key: bytes) -> str:
    return bech32_encode("nsec", private_key)


def _key_from_text(text: str) -> bytes:
    """Accept a 64-char hex key or an ``nsec1…`` / ``npub1…`` bech32 key."""
    text = text.strip()
    if text.startswith(("nsec1", "npub1")):
        _hrp, payload = bech32_decode(text)
        return payload
    raw = bytes.fromhex(text)
    if len(raw) != 32:
        raise ValueError("A Nostr key is 32 bytes (64 hex chars).")
    return raw


# ── identity ─────────────────────────────────────────────────────────

DEFAULT_KEY_FILE = ".nostr/identity.json"


@dataclass
class NostrIdentity:
    """A keypair plus the event signing this agent needs."""

    private_key: bytes

    @property
    def public_key(self) -> bytes:
        return public_key(self.private_key)

    @property
    def pubkey_hex(self) -> str:
        return self.public_key.hex()

    @property
    def npub(self) -> str:
        return to_npub(self.public_key)

    @property
    def nsec(self) -> str:
        return to_nsec(self.private_key)

    # ── NIP-01 events ────────────────────────────────────────────────

    def event_id(self, kind: int, content: str, tags: list, created_at: int) -> str:
        """The NIP-01 event id: sha256 over the canonical serialization."""
        serialized = json.dumps(
            [0, self.pubkey_hex, created_at, kind, tags, content],
            separators=(",", ":"),
            ensure_ascii=False,
        )
        return hashlib.sha256(serialized.encode("utf-8")).hexdigest()

    def sign_event(
        self,
        kind: int,
        content: str,
        tags: list | None = None,
        created_at: int | None = None,
    ) -> dict:
        """Build a complete, signed Nostr event ready to publish."""
        tags = tags or []
        created_at = created_at if created_at is not None else int(time.time())
        event_id = self.event_id(kind, content, tags, created_at)
        signature = sign(bytes.fromhex(event_id), self.private_key)
        return {
            "id": event_id,
            "pubkey": self.pubkey_hex,
            "created_at": created_at,
            "kind": kind,
            "tags": tags,
            "content": content,
            "sig": signature.hex(),
        }

    @staticmethod
    def verify_event(event: dict) -> bool:
        """Check an inbound event's id and signature."""
        try:
            serialized = json.dumps(
                [
                    0,
                    event["pubkey"],
                    event["created_at"],
                    event["kind"],
                    event["tags"],
                    event["content"],
                ],
                separators=(",", ":"),
                ensure_ascii=False,
            )
            expected_id = hashlib.sha256(serialized.encode("utf-8")).hexdigest()
            if expected_id != event.get("id"):
                return False
            return verify(
                bytes.fromhex(expected_id),
                bytes.fromhex(event["pubkey"]),
                bytes.fromhex(event["sig"]),
            )
        except (KeyError, TypeError, ValueError):
            return False


def load_identity(key_file: str | None = None) -> NostrIdentity:
    """Resolve the agent's identity from env, then disk, then generate one.

    A generated key is written to ``NOSTR_KEY_FILE`` (default
    ``.nostr/identity.json``) with mode ``0600`` so the agent keeps the same
    npub across restarts. That file is a private key — the scaffolded
    ``.gitignore`` covers the default path; keep it covered if you move it.
    """
    from_env = os.getenv("NOSTR_PRIVATE_KEY", "").strip()
    if from_env:
        return NostrIdentity(_key_from_text(from_env))

    path = Path(key_file or os.getenv("NOSTR_KEY_FILE") or DEFAULT_KEY_FILE)
    if path.is_file():
        data = json.loads(path.read_text(encoding="utf-8"))
        return NostrIdentity(_key_from_text(data["private_key"]))

    identity = NostrIdentity(generate_private_key())
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "private_key": identity.private_key.hex(),
                "public_key": identity.pubkey_hex,
                "npub": identity.npub,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    os.chmod(path, 0o600)
    return identity


def main() -> None:
    identity = load_identity()
    print(f"npub:   {identity.npub}")
    print(f"pubkey: {identity.pubkey_hex}")
    print(
        "\nThe secret key stays where load_identity() found it "
        f"(NOSTR_PRIVATE_KEY, or {os.getenv('NOSTR_KEY_FILE') or DEFAULT_KEY_FILE})."
    )


if __name__ == "__main__":
    main()
