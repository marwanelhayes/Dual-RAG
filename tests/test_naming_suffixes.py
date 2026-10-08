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


def test_neutral_module_normalization_expands_configured_abbreviations():
    assert naming.normalize_identifier("fpuTopWrapper") == "fpu_top_wrapper"
    assert naming.identifier_tokens("fpu_top_wrapper") == [
        "floating", "point", "unit", "top", "wrapper"]
    assert naming.normalize_module_name("FPU_top_wrapper") == \
        "floating point unit top wrapper"


def test_an_active_low_n_after_an_acronym_is_its_own_token():
    assert naming.identifier_tokens("ARESETn", expand_abbreviations=False) == ["areset", "n"]
    assert naming.identifier_tokens("HRESETn", expand_abbreviations=False) == ["hreset", "n"]
    assert naming.identifier_tokens("MAWAddr", expand_abbreviations=False) == ["maw", "addr"]


def test_a_prefix_shared_by_two_hardware_meanings_is_not_expanded():
    # "wb" is a pipeline's writeback and a Wishbone bus; guessing one mislabels the other.
    assert naming.identifier_tokens("wb_gpio") == ["wb", "gpio"]
    # "rnd" is rounding in an FPU and random in an LFSR.
    assert naming.identifier_tokens("lfsr_rnd") == ["lfsr", "rnd"]
