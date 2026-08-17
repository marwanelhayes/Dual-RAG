"""Tests for deterministic table-derived specification signal extraction."""

import json

import naming
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
        "columns": columns, "rows": rows,
    }


def test_signal_rows_keep_original_name_and_structured_context():
    records = [table_chunk(
        ["Signal", "Width", "Direction", "Description"],
        [["rm_i", "3", "input", "selects IEEE rounding mode"],
         ["fpResultO", "[31:0]", "out", "floating-point result"]])]

    signals = spec_signals.build_spec_signals(records)

    assert [signal["original_name"] for signal in signals] == ["rm_i", "fpResultO"]
    assert signals[0]["normalized_name"] == "rm_i"
    assert signals[0]["normalized_tokens"] == ["rounding", "mode", "input"]
    assert signals[0]["width"] == 3 and signals[0]["direction"] == "input"
    assert signals[1]["normalized_name"] == "fp_result_o"
    assert signals[1]["width"] == 32 and signals[1]["direction"] == "output"
    assert signals[1]["section_title"] == "Floating Point Unit"
    assert signals[1]["subsection_title"] == "Interface"
    assert signals[1]["table_id"] == "fpu_spec::table::2.1"
    assert signals[1]["page"] == 4 and signals[1]["source_file"] == "fpu.pdf"


def test_unknown_width_and_non_direction_are_preserved_without_inference():
    signals = spec_signals.build_spec_signals([table_chunk(
        ["Name", "Width", "Direction", "Meaning"],
        [["result", "DATA_WIDTH", "Manager", "result bus"]])])

    assert signals[0]["width"] == "DATA_WIDTH"
    assert signals[0]["width_source"] == "DATA_WIDTH"
    assert signals[0]["direction"] is None
    assert signals[0]["direction_source"] == "Manager"
    assert signals[0]["description"] == "result bus"


def test_mnemonic_tables_yield_instruction_rows_named_by_the_mnemonic():
    record = table_chunk(
        ["Rounding Mode", "Mnemonic", "Meaning"],
        [["000", "RNE", "Round to Nearest, ties to Even"],
         ["001", "RTZ", "Round towards Zero"]])
    record["caption"] = "Floating Point Rounding Modes"

    signals = spec_signals.build_spec_signals([record])

    assert [signal["original_name"] for signal in signals] == ["RNE", "RTZ"]
    assert {signal["row_kind"] for signal in signals} == {spec_signals.INSTRUCTION_ROW}
    assert signals[0]["description"] == "Round to Nearest, ties to Even"


def test_prose_mnemonics_are_extracted_without_section_references():
    prose = {
        "chunk_id": "isa#0082#015", "chunk_type": "prose", "document_id": "isa",
        "source_file": "isa.pdf", "page": 119, "section_id": "isa::sec::0001",
        "section_title": "Single-Precision Floating-Point",
        "text": "FADD.S rs1, rs2, frm rd NV, OF, NX FCVT.W.S rs1 rd. See A15.3 and B2.2.",
    }

    signals = spec_signals.build_spec_signals([prose])
    names = [signal["original_name"] for signal in signals]

    assert names == ["FADD.S", "FCVT.W.S"]
    assert all(signal["row_kind"] == spec_signals.INSTRUCTION_ROW for signal in signals)
    assert signals[0]["page"] == 119 and signals[0]["chunk_id"] == "isa#0082#015"
    assert signals[0]["normalized_name"] == "fadd_s"


def test_non_table_records_and_tables_without_signal_name_headers_are_ignored():
    prose = {"chunk_type": "prose", "text": "AWVALID is a signal."}
    encoding = table_chunk(["Opcode", "Meaning"], [["ADD", "addition"]])
    properties = table_chunk(
        ["Name", "Values", "Description"],
        [["Num_RP_AWW", "1-8", "number of resource planes"]])
    properties["caption"] = "Resource plane properties"

    signals = spec_signals.build_spec_signals([prose, encoding, properties])

    assert [signal["original_name"] for signal in signals] == ["ADD"]
    assert signals[0]["row_kind"] == spec_signals.INSTRUCTION_ROW
    assert not [signal for signal in signals
                if signal["row_kind"] != spec_signals.INSTRUCTION_ROW]


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
    assert payload["schema_version"] == spec_signals.SCHEMA_VERSION
    assert payload["signals"] == first


def test_neutral_module_normalization_expands_configured_abbreviations():
    assert naming.normalize_identifier("fpuTopWrapper") == "fpu_top_wrapper"
    assert naming.identifier_tokens("fpu_top_wrapper") == [
        "floating", "point", "unit", "top", "wrapper"]
    assert naming.normalize_module_name("FPU_top_wrapper") == \
        "floating point unit top wrapper"


def test_register_address_listings_yield_register_field_rows():
    """Admit allocation tables that name registers by address without port columns."""
    listing = table_chunk(
        ["Number", "Privilege", "Name", "Description"],
        [["0xF13", "MRO", "mimpid", "Implementation ID."],
         ["0x300", "MRW", "mstatus", "Machine status register."]])
    listing["caption"] = "Currently allocated RISC-V machine-level CSR addresses."

    signals = spec_signals.build_spec_signals([listing])

    assert [signal["original_name"] for signal in signals] == ["mimpid", "mstatus"]
    assert {signal["row_kind"] for signal in signals} == {spec_signals.REGISTER_FIELD_ROW}
    assert signals[0]["description"] == "Implementation ID."


def test_name_only_property_tables_stay_out_without_an_address_column():
    """Keep value-property tables rejected so the widened rule stays register-specific."""
    properties = table_chunk(
        ["Name", "Values", "Default", "Description"],
        [["Num_RP_AWW", "1-8", "4", "number of resource planes"]])
    properties["caption"] = "Resource plane properties"

    assert spec_signals.build_spec_signals([properties]) == []
