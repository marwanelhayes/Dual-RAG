"""Hermetic tests for deterministic document and chunk role scoring."""

import roles


NORMATIVE = ("The Manager must not wait for AWREADY before asserting AWVALID. "
             "Implementations shall treat reserved encodings as illegal.")
EXPLANATORY = ("Overview: this guide walks through an example write transaction. "
               "For instance, suppose the master drives the address first.")


def test_normative_wording_outranks_the_other_document_roles():
    scores = roles.document_role_scores([NORMATIVE])

    assert roles.leading_role(scores) == "normative"
    assert scores["normative"] > scores["explanatory"]
    assert round(sum(scores.values()), 4) == 1.0


def test_explanatory_wording_outranks_normative_wording():
    scores = roles.document_role_scores([EXPLANATORY])

    assert roles.leading_role(scores) == "explanatory"
    assert scores["explanatory"] > scores["normative"]


def test_requirement_wording_names_the_chunk_role():
    scores = roles.chunk_role_scores("The master shall assert AWVALID.")

    assert roles.leading_role(scores) == "requirement"


def test_a_contested_chunk_reports_scores_without_naming_a_role():
    scores = roles.chunk_role_scores(
        "Code must use a FENCE. For example, the write occurs before the read.")

    assert roles.leading_role(scores) is None
    assert scores["requirement"] and scores["example"] and scores["behavior"]


def test_unmatched_text_scores_zero_and_names_no_role():
    scores = roles.chunk_role_scores("RV32 RV64 Mnemonic cbo.zero base")

    assert set(scores.values()) == {0.0}
    assert roles.leading_role(scores) is None


def test_a_table_earns_a_property_score_without_role_wording():
    scores = roles.chunk_role_scores("| Name | Value |\n|---|---|\n| a | 1 |",
                                     chunk_type="table")

    assert roles.leading_role(scores) == "property"


def test_margin_governs_whether_a_close_lead_is_named():
    scores = {"requirement": 0.52, "example": 0.48}

    assert roles.leading_role(scores) is None
    assert roles.leading_role(scores, margin=0.01) == "requirement"
