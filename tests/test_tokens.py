"""Tokens of normalized ODP text."""

from odp_kml.tokens import phrase_signature


def test_phrase_signature_abstracts_numbers_and_idents_but_keeps_keywords():
    phrase = "to intercept IPL VORTAC R-009 to\n3000.5 MSL"

    assert phrase_signature(phrase) == "to intercept <id> vortac r-<n> to <n> msl"
