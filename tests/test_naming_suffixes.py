"""Role letters must expand as suffixes only, never inside a severed camel-case word."""

import configure  # noqa: F401  installs the project abbreviation vocabulary
import naming


def test_a_trailing_role_letter_still_expands_to_its_direction():
    assert naming.identifier_tokens("frm_i") == ["frm", "input"]
    assert naming.identifier_tokens("data_o") == ["data", "output"]


def test_a_leading_role_letter_stays_part_of_the_word_it_was_split_from():
    assert naming.identifier_tokens("ICacheHit") == ["i", "cache", "hit"]


def test_a_role_letter_alone_is_not_a_direction():
    assert naming.identifier_tokens("i") == ["i"]


def test_project_vocabulary_reaches_the_neutral_normalizer_through_config():
    assert naming.identifier_tokens("PCSrcE") == [
        "program", "counter", "source", "e"]
    assert naming.identifier_tokens("csr_file") == [
        "control", "status", "register", "file"]
