"""Central configuration for every ChipVer pipeline stage."""

import os
from pathlib import Path

from naming import set_abbreviation_aliases

# Anchor paths to the project root so launch location cannot change behavior.
ROOT = Path(__file__).resolve().parent.parent
INGEST_DIR = ROOT / "Ingest"  # Read-only source documents.
OUTPUT_DIR = ROOT / "output"  # Generated pipeline artifacts.
CHUNKS_PATH = OUTPUT_DIR / "chunks.jsonl"
SPEC_HIERARCHY_PATH = OUTPUT_DIR / "spec_hierarchy.json"
SPEC_SIGNALS_PATH = OUTPUT_DIR / "spec_signals.json"

# Keep index data and its content-hash manifest separate for resumable embedding.
INDEX_DIR = OUTPUT_DIR / "spec_index"
MANIFEST_PATH = OUTPUT_DIR / "embed_manifest.json"

# Embedding settings fit the BGE encoder within the 4 GB GPU budget; sweeps override them.
EMBED_MODEL = os.environ.get("EMBED_MODEL", "BAAI/bge-small-en-v1.5")
EMBED_DEVICE = "cuda"
EMBED_BATCH = int(os.environ.get("EMBED_BATCH", 64))

# BGE's asymmetric training requires this prefix only on queries; passages stay bare.
EMBED_QUERY_PREFIX = os.environ.get(
    "EMBED_QUERY_PREFIX", "Represent this sentence for searching relevant passages: ")
EMBED_DOCUMENT_PREFIX = os.environ.get("EMBED_DOCUMENT_PREFIX", "")

# Encoder-token caps picked by the sweep/chunk_tokens grid; the environment overrides them.
MAX_CHUNK_TOKENS = int(os.environ.get("MAX_CHUNK_TOKENS", 384))
MAX_TABLE_CHUNK_TOKENS = int(os.environ.get("MAX_TABLE_CHUNK_TOKENS", 128))
# Header plus body must fit the encoder window; a longer chunk fails the run.
ENCODER_MAX_TOKENS = int(os.environ.get("ENCODER_MAX_TOKENS", 512))
OVERLAP_SENTENCES = 2
REVISION_SCAN_PAGES = 4

# Sample enough pages to detect recurring PDF headers and footers cheaply.
FURNITURE_SAMPLE_PAGES = 40
FURNITURE_THRESHOLD = 0.6
# Header and footer blocks sit within this many points of their recurring band edge.
FURNITURE_BAND_TOLERANCE = 2
# A heading block may start this many points above its outline destination point.
HEADING_POINT_SLACK = 2

# Table geometry is measured in PDF points.
COLUMN_GAP = 25
COLUMN_SLACK = 6
TABLE_EDGE_SLACK = 6
MIN_TABLE_HEIGHT = 4

# A caption binds to the nearest ruled table either above or below its body.
CAPTION_SLACK = 2
CAPTION_MAX_GAP = 60
# A caption this far down the page carries its table over the page break.
CAPTION_PAGE_BREAK = 0.82

# Heading detection used only when a PDF ships no outline.
HEADING_SAMPLE_PAGES = 40
HEADING_LARGER = 1.08
HEADING_SIZE_SLACK = 0.2
HEADING_MAX_CHARS = 140
# A heading repeated more than this is a table header or running furniture.
HEADING_MAX_REPEATS = 4

# Exclude navigation-only sections from retrieval content.
SKIP_TITLES = {"contents", "table of contents", "index", "bibliography"}

# These abbreviations do not terminate sentences.
ABBREVIATIONS = {"e.g", "i.e", "cf", "vs", "etc", "Fig", "Sec", "No", "Dr", "Mr", "approx"}

# Narrow normative wording ranks descriptive manuals below tutorials, so widen it.
ROLE_DOCUMENT_VOCABULARY = {
    "normative": ["shall", "must", r"require\w*", r"reserved?", "illegal", "compliant",
                  "prohibited", "mandatory", "is defined", r"specif\w*",
                  r"implementations?"],
    "architecture": [r"pipelines?", r"modules?", r"stages?", r"connect\w*",
                     r"instantiat\w+", "datapath", "microarchitecture",
                     "block diagram", "top-level"],
    # Explaining a specification quotes its rules, so intent shows in demonstration.
    "explanatory": ["example", "overview", "tutorial", "guide", "introduction",
                    "for instance", "walkthrough", "suppose", r"shows?", r"diagrams?",
                    "in this case", "you"],
}
ROLE_DOCUMENT_WEIGHTS = {"normative": 1.0, "architecture": 1.0, "explanatory": 1.0}

# Fuse broad lexical and dense candidate sets before the expensive precision pass.
RETRIEVE_CANDIDATES = 30
RETRIEVE_TOP_K = 5

# A document that defines behavior outranks one that only explains it.
ROLE_AUTHORITY = {"normative": 1.0, "architecture": 0.6, "explanatory": 0.2}
# Authority is centered per shortlist, so it breaks ties without outvoting relevance.
RERANK_AUTHORITY_WEIGHT = 1.0

# Rerank only the shortlist because cross-encoders score query-document pairs jointly.
RERANK_MODEL = "cross-encoder/ms-marco-MiniLM-L-6-v2"
RERANK_DEVICE = "cuda"
RERANK_BATCH = 32

# Verilator defines every RTL module, source path, and line range exposed to the agent.
RTL_DIR = ROOT / "rtl"
RTL_PATH = OUTPUT_DIR / "rtl.json"

# Index English RTL surfaces separately so they never compete with specification chunks.
RTL_CHUNKS_PATH = OUTPUT_DIR / "rtl_chunks.jsonl"
RTL_INDEX_DIR = OUTPUT_DIR / "rtl_index"
# Validate the RTL index manifest before allowing dense lookup against compiler artifacts.
RTL_MANIFEST_PATH = OUTPUT_DIR / "rtl_embed_manifest.json"
# Largest surface that keeps every measured RTL chunk inside the encoder's 512 tokens.
RTL_CHUNK_CHARS = int(os.environ.get("RTL_CHUNK_CHARS", 1200))
RTL_TOP_MODULE = "riscv_top"
VERILATOR_ARGS = ["-Wno-fatal", "--top-module", RTL_TOP_MODULE,
                  "-y", str(RTL_DIR), str(RTL_DIR / "shared_pkg.sv"),
                  str(RTL_DIR / "riscv_top.sv")]

# This design's vocabulary, kept out of the shared normalizer so another corpus can swap it.
RTL_ABBREVIATIONS = {
    "alu": ("arithmetic", "logic", "unit"),
    "csr": ("control", "status", "register"),
    "fp": ("floating", "point"),
    "fpu": ("floating", "point", "unit"),
    "imm": ("immediate",),
    "insn": ("instruction",),
    "instr": ("instruction",),
    "mem": ("memory",),
    "pc": ("program", "counter"),
    "rm": ("rounding", "mode"),
    "rnd": ("rounding",),
    "wb": ("writeback",),
}
set_abbreviation_aliases(RTL_ABBREVIATIONS)

# Suggested look-alikes weigh edit similarity below exact token overlap.
BRIDGE_EDIT_WEIGHT = 0.8
BRIDGE_PROVENANCE_SITES = 2
# An interface prefix is 1-3 characters, shared by at least 2 ports naming 4+ character specs.
BRIDGE_PREFIX_MAX_CHARS = 3
BRIDGE_PREFIX_MIN_NAME = 4
BRIDGE_PREFIX_MIN_PORTS = 2
# Unresolved shortlists may guide inspection without becoming persisted alignments.
BRIDGE_QUERY_CANDIDATES = 3
BRIDGE_PATH = OUTPUT_DIR / "bridge.json"

# Benchmark against hand-checked answers instead of transcript-level inspection.
GOLD_PATH = ROOT / "eval" / "gold_questions.json"
BENCH_PATH = OUTPUT_DIR / "bench.json"
# Retrieval is scored on its own so lane failures are not hidden by answer wording.
QUESTION_SET_PATH = ROOT / "eval" / "question_set.json"
RETRIEVAL_PATH = OUTPUT_DIR / "retrieval_bench.json"

# A single small local model reasons only over the final bounded evidence package.
LOCAL_LLM_MODEL = "qwen3:4b"
LOCAL_LLM_HOST = "http://localhost:11434"

# Whole passages from both lanes fit one 8,192-token window.
AGENT_NUM_CTX = 8192
AGENT_TIMEOUT_S = 300
# Passages shown per lane, whole (sweep/agent_k: 8/8 max ~5.9k bge tokens of 8,192).
AGENT_RTL_SEARCH_K = 8
AGENT_SPEC_SEARCH_K = 8

# A fixed sentinel lets the graph route insufficient evidence deterministically.
AGENT_INSUFFICIENT = "INSUFFICIENT"
