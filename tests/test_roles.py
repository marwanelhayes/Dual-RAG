"""Hermetic tests for deterministic document role scoring."""

import roles


NORMATIVE = ("The Manager must not wait for AWREADY before asserting AWVALID. "
             "Implementations shall treat reserved encodings as illegal.")
EXPLANATORY = ("Overview: this guide walks through an example write transaction. "
               "For instance, suppose the master drives the address first.")


def test_normative_wording_outranks_the_other_document_roles():
    scores = roles.document_role_scores([NORMATIVE])

    assert max(scores, key=scores.get) == "normative"
    assert scores["normative"] > scores["explanatory"]
    assert round(sum(scores.values()), 4) == 1.0


def test_explanatory_wording_outranks_normative_wording():
    scores = roles.document_role_scores([EXPLANATORY])

    assert max(scores, key=scores.get) == "explanatory"
    assert scores["explanatory"] > scores["normative"]


def test_roles_score_documents_only():
    """Chunk roles had no reader, so only document scoring remains."""
    assert not hasattr(roles, "chunk_role_scores") and not hasattr(roles, "leading_role")
