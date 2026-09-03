"""Verification of the NPort decryptor against the real estate.

This asks a question about *our devices*, not about this repository's code: do
all the terminal server backups we hold still decrypt? The backups are live
configurations - addresses, server names and other site detail - so they cannot
be committed here, and this therefore never runs in CI.

That is deliberate rather than an oversight. Correctness of the decryptor is
established in ``test_nport.py``, which runs everywhere: vendor generated block
vectors, and a real device export checked byte for byte against the decryption
that Moxa's own ``mcc_tool`` produced from it (``tests/data``). A previous test
here re-derived that same agreement by shelling out to ``tools/ts_decrypt``,
which needs Moxa's proprietary ``dsci_mcc.so``; the committed pair asserts it
permanently and without the vendor library, so that test has been removed.

Run this by hand when new hardware or new firmware arrives::

    NPORT_CORPUS=/path/to/backups pytest tests/test_nport_estate.py

There is no default path, because a default that only exists on one developer's
machine reads as coverage while silently testing nothing.
"""

import os
from pathlib import Path

import pytest

from dls_backup_bl import nport


def corpus_files() -> list[Path]:
    corpus = os.environ.get("NPORT_CORPUS")
    return sorted(Path(corpus).glob("*.dec")) if corpus else []


@pytest.mark.skipif(
    not corpus_files(),
    reason="set NPORT_CORPUS to a directory of *.dec backups to run this",
)
def test_every_real_backup_passes_its_checksum():
    """The vendor's own integrity check over the whole estate."""
    for dec in corpus_files():
        plain = nport.decrypt(dec.read_bytes())
        assert b"[NPort Configuration File]" in plain[:64], dec.name
