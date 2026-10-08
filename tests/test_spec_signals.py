"""Tests for deterministic table-derived specification signal extraction."""

import json

import pytest

import spec_signals


def table_chunk(columns, rows):
    """Build a structured table chunk with complete signal provenance."""
    return {
        "chunk_id": "fpu_spec#table#2.1", "chunk_type": "table",
        "document_id": "fpu_spec", "source_file": "fpu.pdf",
        "section_id": "fpu_spec::sec::0000", "section_title": "Floating Point Unit",
        "subsection_id": "fpu_spec::sec::0001", "subsection_title": "Interface",
        "table_id": "fpu_spec::table::2.1", "table_ids": ["fpu_spec::table::2.1"],
        "table_label": "2.1", "caption": "Interface signals", "page": 4,
        "columns": columns, "rows": rows, "row_start": 0,
        "hierarchy_id": "fpu_spec::sec::0001",
        "section_path": ["Floating Point Unit", "Interface"],
    }


def test_signal_rows_keep_their_name_cells_and_structured_context():
    records = [table_chunk(
        ["Signal", "Width", "Description"],
        [["rm_i", "3", "selects IEEE rounding mode"],
         ["fpResultO", "[31:0]", "floating-point result"]])]

    signals = spec_signals.build_spec_signals(records)

    assert [signal["name"] for signal in signals] == ["rm_i", "fpResultO"]
    assert signals[0]["normalized_name"] == "rm_i"
    assert signals[0]["normalized_tokens"] == ["rounding", "mode", "input"]
    assert signals[0]["width"] == "3" and signals[1]["width"] == "[31:0]"
    assert signals[1]["normalized_name"] == "fp_result_o"
    assert signals[1]["table_id"] == "fpu_spec::table::2.1"
    assert signals[1]["page"] == 4 and signals[1]["source_file"] == "fpu.pdf"


def test_width_and_source_cells_are_stored_as_written():
    signals = spec_signals.build_spec_signals([table_chunk(
        ["Name", "Width", "Source", "Meaning"],
        [["result", "DATA_WIDTH", "Tx", "result bus"]])])

    assert signals[0]["width"] == "DATA_WIDTH"
    assert signals[0]["source"] == "Tx"
    assert signals[0]["description"] == "result bus"


def test_instruction_tables_and_prose_mnemonics_are_not_signals():
    mnemonics = table_chunk(
        ["Rounding Mode", "Mnemonic", "Meaning"],
        [["000", "RNE", "Round to Nearest, ties to Even"]])
    encoding = table_chunk(["Opcode", "Meaning"], [["ADD", "addition"]])
    prose = {
        "chunk_id": "isa#0082#015", "chunk_type": "prose", "document_id": "isa",
        "source_file": "isa.pdf", "page": 119, "text": "FADD.S rs1, rs2, frm rd.",
    }

    assert spec_signals.build_spec_signals([mnemonics, encoding, prose]) == []


def test_tables_without_signal_name_headers_are_ignored():
    properties = table_chunk(
        ["Name", "Values", "Description"],
        [["Num_RP_AWW", "1-8", "number of resource planes"]])
    properties["caption"] = "Resource plane properties"

    assert spec_signals.build_spec_signals([properties]) == []


def test_build_and_save_are_idempotent(tmp_path):
    records = [table_chunk(["Signal", "Width"], [["rm_i", "3"]])]
    first = spec_signals.build_spec_signals(records)
    second = spec_signals.build_spec_signals(records)
    path = tmp_path / "spec_signals.json"

    spec_signals.save_spec_signals(first, path)
    first_bytes = path.read_bytes()
    spec_signals.save_spec_signals(second, path)

    assert first == second
    assert path.read_bytes() == first_bytes
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["schema_version"] == spec_signals.SCHEMA_VERSION == 7
    assert payload["signals"] == first


def test_register_address_listings_yield_register_field_rows():
    """Admit allocation tables that name registers by address without port columns."""
    listing = table_chunk(
        ["Number", "Privilege", "Name", "Description"],
        [["0xF13", "MRO", "mimpid", "Implementation ID."],
         ["0x300", "MRW", "mstatus", "Machine status register."]])
    listing["caption"] = "Currently allocated RISC-V machine-level CSR addresses."

    signals = spec_signals.build_spec_signals([listing])

    assert [signal["name"] for signal in signals] == ["mimpid", "mstatus"]
    assert signals[0]["description"] == "Implementation ID."


def test_name_only_property_tables_stay_out_without_an_address_column():
    """Keep value-property tables rejected so the widened rule stays register-specific."""
    properties = table_chunk(
        ["Name", "Values", "Default", "Description"],
        [["Num_RP_AWW", "1-8", "4", "number of resource planes"]])
    properties["caption"] = "Resource plane properties"

    assert spec_signals.build_spec_signals([properties]) == []


def test_signal_records_carry_no_unread_fields():
    """Keep only fields some reader uses; these four had none."""
    signals = spec_signals.build_spec_signals([table_chunk(
        ["Signal", "Width", "Direction"], [["rm_i", "3", "input"]])])

    assert not {"signal_name", "content_hash", "width_source",
                "direction_source"} & set(signals[0])


def test_table_chunk_missing_a_contract_field_fails_loudly():
    """Ingest guarantees document_id on every chunk; a gap is an ingest bug."""
    record = table_chunk(["Signal"], [["rm_i"]])
    del record["document_id"]

    with pytest.raises(KeyError):
        spec_signals.build_spec_signals([record])


def test_bit_slices_and_dotted_names_are_not_signals():
    signals = spec_signals.build_spec_signals([table_chunk(
        ["Signal", "Width"],
        [["ACADDR[51]", "1"], ["vsew[2:0]", "3"], ["mstatus.MIE", "1"], ["AWVALID", "1"]])])

    assert [signal["name"] for signal in signals] == ["AWVALID"]


def test_an_address_table_row_without_an_address_is_a_group_label():
    listing = table_chunk(
        ["Number", "Privilege", "Name", "Description"],
        [["", "", "Supe", "rvisor Trap Setup"], ["0x100", "SRW", "sstatus", "Status."]])

    signals = spec_signals.build_spec_signals([listing])

    assert [signal["name"] for signal in signals] == ["sstatus"]


def test_a_signal_caption_alone_does_not_admit_a_name_only_table():
    listing = table_chunk(["Name", "Description"], [["lock", "Lock state"]])
    listing["caption"] = "Interface signals"

    assert spec_signals.build_spec_signals([listing]) == []


def test_width_comes_only_from_the_row_that_states_it():
    table = table_chunk(["Signal", "Description"], [["AWID", "Write id"]])
    prose = {"chunk_id": "p", "chunk_type": "prose", "document_id": "fpu_spec",
             "text": "The AWID[3:0] bus carries the id."}

    assert spec_signals.build_spec_signals([table, prose])[0]["width"] is None


def test_a_bits_column_is_a_position_not_a_width():
    fields = table_chunk(["Bits", "Name", "Description"], [["[5:4]", "CBIE", "Enable."]])

    assert spec_signals.build_spec_signals([fields]) == []


def test_a_name_repeated_across_windows_of_one_table_keeps_every_row():
    first = table_chunk(["Signal", "Width"], [["AWID", "4"], ["AWLEN", "8"]])
    second = table_chunk(["Signal", "Width"], [["AWID", "8"]])
    first["row_start"], second["row_start"] = 0, 2
    second["chunk_id"] = "fpu_spec#table#2.1#0002"

    signals = spec_signals.build_spec_signals([first, second])

    assert [(signal["name"], signal["row_index"]) for signal in signals] == [
        ("AWID", 1), ("AWLEN", 2), ("AWID", 3)]
    assert signals[2]["signal_id"] == signals[0]["signal_id"] + "::2"


def test_signal_records_follow_the_consolidated_contract():
    signal = spec_signals.build_spec_signals([table_chunk(
        ["Signal", "Width", "Source", "Description"],
        [["AWID", "[3:0]", "Manager", "Write id"]])])[0]

    assert signal["name"] == "AWID" and signal["width"] == "[3:0]"
    assert signal["source"] == "Manager"
    assert signal["hierarchy_id"] == "fpu_spec::sec::0001"
    assert signal["section_path"] == ["Floating Point Unit", "Interface"]
    assert not {"id", "original_name", "row_kind", "direction", "source_role",
                "source_role_source", "section_title", "subsection_title",
                "section_id", "subsection_id"} & set(signal)


def test_address_table_rows_carry_their_address_cell_as_written():
    listing = table_chunk(
        ["Number", "Privilege", "Name", "Description"],
        [["0x343", "MRW", "mtval", "Machine trap value."]])
    ports = table_chunk(["Signal", "Width"], [["AWVALID", "1"]])

    signals = spec_signals.build_spec_signals([listing, ports])

    assert [signal["address"] for signal in signals] == ["0x343", None]
