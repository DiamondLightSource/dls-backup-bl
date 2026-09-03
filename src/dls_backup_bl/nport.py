"""Decrypt Moxa NPort terminal server configuration backups.

An NPort 6000 exports its configuration from the web UI as ``/Config.txt``:
a 16 byte plaintext header followed by the configuration text encrypted with
Rijndael-256 in ECB mode.  ``dls-backup-bl`` saves that verbatim as
``<server>_config.dec``, which makes the backups opaque - you cannot diff two
of them or read one without putting it back on a device.

This module decrypts them locally, with no device and no vendor library.

The cipher is Rijndael with a 256 bit block *and* a 256 bit key (Nb = Nk = 8,
14 rounds).  Note this is **not** AES: AES fixes the block size at 128 bits, so
no mainstream crypto library implements it and it has to be spelled out here.
The implementation below is the original Rijndael reference algorithm, which
works on a 4 x Nb byte matrix.  Moxa loads that matrix - and the key - in row
major order (``m[i][j] = buf[8 * i + j]``) rather than the column major order
that FIPS-197 later standardised, so the byte ordering here is deliberate.

The key is a 32 byte constant baked into the vendor tooling, with its first
``len(psk)`` bytes overwritten by the device's configuration pre-shared key.
Devices leave that key at the factory default ``moxa`` unless it is explicitly
changed on the Export Configuration page.

Verified against Moxa's own implementation (``CfgAESDecrypt`` in the mcc_tool
plugin ``dsci_mcc.so``): block by block against vendor generated vectors, and
whole file against a real device export decrypted by ``mcc_tool`` itself - see
``tests/test_nport.py`` and ``tests/data``.

As a library::

    text = nport.decrypt(Path("172.23.243.10_config.dec").read_bytes())

From the command line, ``dls-backup-bl --decrypt-file FILE`` converts one saved
backup, and ``--decrypt`` / ``--decrypt-only`` ask a normal backup run to write
the readable copy as well as, or instead of, the encrypted one.
"""

from pathlib import Path

# The vendor's 32 byte key constant.  The pre-shared key is written over the
# front of it; the tail below supplies the remaining bytes.
CFG_ENCRYPT_KEY = bytes.fromhex(
    "2d375841a85cc77525a18747f0a7052b4c8b9e70dab30dcb51d0d3125ac43c97"
)
#: Factory default configuration pre-shared key.
DEFAULT_PSK = "moxa"

#: Product magics that identify an encrypted configuration export, as listed by
#: the vendor tooling.  A model whose magic is missing here is refused by
#: :func:`is_encrypted_config` even though its payload would decrypt fine, so
#: add to this list if a new one turns up.
MAGICS = (b"6000", b"5000", b"500A", b"8000", b"NEE1", b"NEE2", b"NEE3")

# Opaque vendor tags, not version numbers you can order or compare.  Every
# backup taken at Diamond so far is v2; the v1 branch comes from the vendor code
# and has never been checked against a real v1 file.
_VERSION_V1 = 0x00000001  # 12 byte header, payload runs to the end of the file
_VERSION_V2 = 0x01010000  # 16 byte header carrying an explicit payload length

# Header layout, all integers little endian:
#
#   offset  size  field
#   0       4     product magic, one of MAGICS
#   4       4     format version, _VERSION_V1 or _VERSION_V2
#   8       4     checksum of the decrypted payload, see checksum()
#   12      4     payload length in bytes - v2 only, v1's header ends at 12
#
# Everything after the header is ciphertext, a whole number of blocks.

BLOCK_SIZE = 32  # 256 bit Rijndael block


class NPortConfigError(Exception):
    """A file could not be decrypted as an NPort configuration export."""


class ChecksumError(NPortConfigError):
    """Decrypted, but the plaintext checksum disagrees with the header.

    Almost always means the pre-shared key is wrong.
    """


# --- Rijndael-256 -----------------------------------------------------------
#
# What follows is a transcription of the Rijndael reference implementation and
# keeps that code's own names (tk, Nb, Nk, rcon).  It is deliberately not
# idiomatic Python and is not worth reading line by line: the vendor generated
# vectors in tests/test_nport.py are the specification.  Do not tidy any of it
# without running them.


def _make_sbox() -> list[int]:
    """Build the Rijndael S-box from its algebraic definition."""
    p = q = 1
    sbox = [0] * 256
    while True:
        p ^= ((p << 1) & 0xFF) ^ (0x1B if p & 0x80 else 0)
        q ^= (q << 1) & 0xFF
        q ^= (q << 2) & 0xFF
        q ^= (q << 4) & 0xFF
        if q & 0x80:
            q ^= 0x09
        x = (
            q
            ^ ((q << 1) | (q >> 7))
            ^ ((q << 2) | (q >> 6))
            ^ ((q << 3) | (q >> 5))
            ^ ((q << 4) | (q >> 4))
        )
        sbox[p] = (x ^ 0x63) & 0xFF
        if p == 1:
            break
    sbox[0] = 0x63
    return sbox


_S = _make_sbox()
_SI = [0] * 256
for _i, _v in enumerate(_S):
    _SI[_v] = _i


def _mul(a: int, b: int) -> int:
    """Multiply in GF(2^8) modulo the Rijndael polynomial."""
    result = 0
    while a:
        if a & 1:
            result ^= b
        b <<= 1
        if b & 0x100:
            b ^= 0x11B
        a >>= 1
    return result


# InvMixColumn multiplies by the fixed polynomial {0e,0b,0d,09}; precompute it.
_MUL_E = bytes(_mul(0x0E, x) for x in range(256))
_MUL_B = bytes(_mul(0x0B, x) for x in range(256))
_MUL_D = bytes(_mul(0x0D, x) for x in range(256))
_MUL_9 = bytes(_mul(0x09, x) for x in range(256))

_RCON = bytes.fromhex("01020408102040801b366cd8ab4d9a2f5ebc63c697356ad4b37dfaefc591")

_NB = 8  # block size in 32 bit columns
_NK = 8  # key size in 32 bit columns
_ROUNDS = 14
# Encrypting, Rijndael shifts row i left by (0, 1, 3, 4) when Nb = 8.
# Decrypting shifts back, i.e. by _NB minus each of those.
_DEC_SHIFTS = (0, 7, 5, 4)

# The state is held flat in row major order: state[8 * row + col].  Decryption's
# InvShiftRow is then a fixed permutation of those 32 positions, which lets it be
# folded into the inverse S-box lookup.
_INV_SHIFT = [
    8 * row + (col + _DEC_SHIFTS[row]) % _NB for row in range(4) for col in range(_NB)
]


def _key_schedule(key: bytes) -> list[list[int]]:
    """Expand a 32 byte key into ``_ROUNDS + 1`` flat 32 byte round keys."""
    # tk is the key as a 4 x _NK matrix, loaded row major like the state.
    tk = [[key[_NK * i + j] for j in range(_NK)] for i in range(4)]
    total = (_ROUNDS + 1) * _NB
    flat = [0] * (4 * total)  # flat[row * total + column] across all round keys
    t = 0
    rcon = 0

    def emit() -> None:
        nonlocal t
        for j in range(_NK):
            if t >= total:
                return
            for i in range(4):
                flat[i * total + t] = tk[i][j]
            t += 1

    emit()
    while t < total:
        for i in range(4):
            tk[i][0] ^= _S[tk[(i + 1) % 4][_NK - 1]]
        tk[0][0] ^= _RCON[rcon]
        rcon += 1
        # _NK == 8 takes the extra S-box pass halfway through the column set
        for j in range(1, _NK // 2):
            for i in range(4):
                tk[i][j] ^= tk[i][j - 1]
        for i in range(4):
            tk[i][_NK // 2] ^= _S[tk[i][_NK // 2 - 1]]
        for j in range(_NK // 2 + 1, _NK):
            for i in range(4):
                tk[i][j] ^= tk[i][j - 1]
        emit()

    return [
        [flat[i * total + r * _NB + j] for i in range(4) for j in range(_NB)]
        for r in range(_ROUNDS + 1)
    ]


def _decrypt_block(block: bytes, round_keys: list[list[int]]) -> bytes:
    """Decrypt one 32 byte Rijndael-256 block."""
    # Row major load is just the raw byte order.
    state = list(block)

    def add_round_key(rk: list[int]) -> None:
        for i in range(32):
            state[i] ^= rk[i]

    def inv_sub_shift() -> None:
        state[:] = [_SI[state[k]] for k in _INV_SHIFT]

    def inv_mix_columns() -> None:
        for j in range(_NB):
            a0, a1, a2, a3 = state[j], state[8 + j], state[16 + j], state[24 + j]
            state[j] = _MUL_E[a0] ^ _MUL_B[a1] ^ _MUL_D[a2] ^ _MUL_9[a3]
            state[8 + j] = _MUL_E[a1] ^ _MUL_B[a2] ^ _MUL_D[a3] ^ _MUL_9[a0]
            state[16 + j] = _MUL_E[a2] ^ _MUL_B[a3] ^ _MUL_D[a0] ^ _MUL_9[a1]
            state[24 + j] = _MUL_E[a3] ^ _MUL_B[a0] ^ _MUL_D[a1] ^ _MUL_9[a2]

    add_round_key(round_keys[_ROUNDS])
    inv_sub_shift()
    for r in range(_ROUNDS - 1, 0, -1):
        add_round_key(round_keys[r])
        inv_mix_columns()
        inv_sub_shift()
    add_round_key(round_keys[0])

    return bytes(state)


def make_key(psk: str = DEFAULT_PSK) -> bytes:
    """Build the 32 byte cipher key for a configuration pre-shared key."""
    try:
        raw = psk.encode("ascii")
    except UnicodeEncodeError as e:
        # do not let this escape as a UnicodeEncodeError: callers catch
        # NPortConfigError to turn a bad key into a message rather than a
        # traceback
        raise NPortConfigError(f"pre-shared key must be ASCII ({e})") from e
    if len(raw) > len(CFG_ENCRYPT_KEY):
        raise NPortConfigError(
            f"pre-shared key is too long ({len(raw)} > {len(CFG_ENCRYPT_KEY)} bytes)"
        )
    return raw + CFG_ENCRYPT_KEY[len(raw) :]


def checksum(data: bytes) -> int:
    """The vendor's integrity check: sum of the LE uint32 words, mod 2**32.

    Taken over the *padded* plaintext - the whole decrypted payload, trailing
    NULs included - so :func:`decrypt` must check it before trimming the text.
    Bytes past a whole number of words are ignored, which never arises here
    because the payload is a multiple of :data:`BLOCK_SIZE`.
    """
    total = sum(
        int.from_bytes(data[i : i + 4], "little") for i in range(0, len(data) & ~3, 4)
    )
    return total & 0xFFFFFFFF


def is_encrypted_config(data: bytes) -> bool:
    """True if ``data`` starts with an encrypted NPort configuration header.

    16 bytes rather than the v1 header's 12: even the shortest real export is a
    header plus at least one 32 byte block.
    """
    return len(data) >= 16 and data[:4] in MAGICS


def decrypt(data: bytes, psk: str = DEFAULT_PSK) -> bytes:
    """Decrypt a raw ``*.dec`` export and return the configuration text.

    Returned verbatim as bytes: the device uses CRLF line endings and writing
    them back out unchanged keeps the ``.ini`` faithful to the original.

    :param data: the whole file, header included.
    :param psk: the device's configuration pre-shared key.
    :raises NPortConfigError: the file is not a recognised export.
    :raises ChecksumError: it decrypted but failed its integrity check,
        which normally means ``psk`` is wrong.
    """
    if not is_encrypted_config(data):
        raise NPortConfigError(
            "not an encrypted NPort configuration (bad magic - already plain text?)"
        )

    version = int.from_bytes(data[4:8], "little")
    header_checksum = int.from_bytes(data[8:12], "little")
    if version == _VERSION_V2:
        header_len = 16
        payload_len = int.from_bytes(data[12:16], "little")
    elif version == _VERSION_V1:
        header_len = 12
        payload_len = len(data) - 12
    else:
        raise NPortConfigError(f"unsupported export version {version:#010x}")

    if payload_len % BLOCK_SIZE or header_len + payload_len > len(data):
        raise NPortConfigError(f"implausible payload length {payload_len}")

    round_keys = _key_schedule(make_key(psk))
    payload = data[header_len : header_len + payload_len]
    plain = b"".join(
        _decrypt_block(payload[i : i + BLOCK_SIZE], round_keys)
        for i in range(0, payload_len, BLOCK_SIZE)
    )

    found = checksum(plain)
    if found != header_checksum:
        raise ChecksumError(
            f"checksum mismatch (got {found:#010x}, header says "
            f"{header_checksum:#010x}) - wrong pre-shared key?"
        )

    # The device NUL pads the last block out to BLOCK_SIZE - there is no
    # standard padding scheme here.  The configuration is 7 bit text and never
    # contains a NUL, so the first one is unambiguously where the padding starts.
    end = plain.find(b"\0")
    if end >= 0:
        plain = plain[:end]
    return plain


#: Suffixes an encrypted export arrives with, stripped when naming the ``.ini``.
_ENCRYPTED_SUFFIXES = (".dec", ".txt")


def default_ini_path(source: Path) -> Path:
    """Where :func:`decrypt_file` writes when given no destination.

    Deliberately not ``Path.with_suffix``: backups are named after the device,
    so ``172.23.243.10_config`` has ``.10_config`` taken as its suffix and would
    come back as ``172.23.243.ini`` - a silently wrong path that could land on
    another device's file.
    """
    name = source.name
    for suffix in _ENCRYPTED_SUFFIXES:
        if name.lower().endswith(suffix):
            name = name[: -len(suffix)]
            break
    return source.with_name(name + ".ini")


def decrypt_file(
    source: Path, dest: Path | None = None, psk: str = DEFAULT_PSK
) -> Path:
    """Decrypt ``source`` to ``dest``, defaulting to :func:`default_ini_path`.
    Returns the path written."""
    if dest is None:
        dest = default_ini_path(source)
    dest.write_bytes(decrypt(source.read_bytes(), psk))
    return dest
