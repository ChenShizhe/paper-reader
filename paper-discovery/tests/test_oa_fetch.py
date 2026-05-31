"""Network-free unit tests for the open-access fallback module.

Live resolver behavior (Unpaywall/PMC/etc.) is exercised separately via a
manual smoke run; these tests cover the pure helpers and the no-network paths
so they are safe for CI.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import oa_fetch  # noqa: E402


def test_title_similarity_basic():
    assert oa_fetch._title_similarity("a b c", "a b c") == 1.0
    assert oa_fetch._title_similarity("a b c", "x y z") == 0.0
    s = oa_fetch._title_similarity(
        "Common population codes produce extremely nonlinear neural manifolds",
        "Common population codes produce extremely nonlinear neural manifolds.",
    )
    assert s > 0.9


def test_is_pdf_guard():
    assert oa_fetch._is_pdf(b"%PDF" + b"0" * (50 * 1024)) is True
    assert oa_fetch._is_pdf(b"%PDF" + b"0" * 10) is False          # too small
    assert oa_fetch._is_pdf(b"<html>" + b"0" * (50 * 1024)) is False  # not a PDF
    assert oa_fetch._is_pdf(None) is False


def test_acquire_requires_identifier(tmp_path):
    res = oa_fetch.acquire(doi=None, out_dir=str(tmp_path), name="x", email="e@e.com")
    assert res.get("error") == "no_identifier_given"
