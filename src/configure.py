"""Central configuration for every ChipVer pipeline stage."""

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

# Embedding settings fit the BGE encoder within the 4 GB GPU budget.
EMBED_MODEL = "BAAI/bge-small-en-v1.5"
EMBED_DEVICE = "cuda"
EMBED_BATCH = 64

# BGE's asymmetric training requires this prefix only on queries.
EMBED_QUERY_PREFIX = "Represent this sentence for searching relevant passages: "

# Character limits keep ingestion independent of any model tokenizer.
MAX_CHUNK_CHARS = 800
# Row windows stay under the encoder's 512 tokens at the densest observed table.
MAX_TABLE_CHUNK_CHARS = 640
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
RETRIEVE_RRF_K = 60

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
# Cap comment prose so large modules do not dominate the RTL index.
RTL_CHUNK_COMMENT_CHARS = 1200
# Largest surface that keeps every measured RTL chunk inside the encoder's 512 tokens.
RTL_CHUNK_CHARS = 1200
RTL_CHUNK_MIN_SOURCE_CHARS = 320
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

# Conservative bridge scoring keeps identity alignment separate from behavioral judgment.
BRIDGE_CONTEXT_CHARS = 1200
BRIDGE_EDIT_WEIGHT = 0.8
BRIDGE_EDIT_MIN = 0.55
BRIDGE_TOKEN_MIN = 0.2
BRIDGE_MIN_CANDIDATES = 3
BRIDGE_MAX_CANDIDATES = 3
BRIDGE_PROVENANCE_SITES = 2
BRIDGE_SIGNAL_WEIGHTS = {
    "name": 0.4,
    "width": 0.15,
    "direction": 0.12,
    "section_module": 0.15,
    "description_usage": 0.12,
    "table_context": 0.06,
}
BRIDGE_STRUCTURAL_MIN = 0.38
BRIDGE_SEMANTIC_WEIGHT = 0.2
BRIDGE_ACCEPT_MIN = 0.62
BRIDGE_MEDIUM_MIN = 0.74
BRIDGE_STRONG_MIN = 0.88
BRIDGE_AMBIGUITY_MARGIN = 0.05
BRIDGE_LLM_MAX_CANDIDATES = 3
BRIDGE_LLM_MAX_CHARS = 2600
BRIDGE_LLM_REASON_CHARS = 240
# Measured complete replies need 95-144 tokens, so 96 truncated the JSON mid-reason.
BRIDGE_LLM_MAX_TOKENS = 256
# Bounded fallbacks keep bridge construction from becoming an extraction workload.
BRIDGE_LLM_MAX_CALLS = 300
BRIDGE_MODULE_WEIGHTS = {"name": 0.5, "context": 0.2, "child_signals": 0.3}
BRIDGE_MODULE_NAME_MIN = 0.45
BRIDGE_MODULE_CONTEXT_MIN = 0.15
BRIDGE_MODULE_CANDIDATES = 3
BRIDGE_MODULE_ACCEPT_MIN = 0.62
BRIDGE_MODULE_PERSIST_NAME_MIN = 0.55
# Unresolved shortlists may guide inspection without becoming persisted alignments.
BRIDGE_QUERY_CANDIDATES = 3
BRIDGE_QUERY_CANDIDATE_MIN = 0.45
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

# Agent budgets fit retrieved evidence in one 4,096-token window.
AGENT_MODEL = "qwen3:4b"
AGENT_NUM_CTX = 4096
AGENT_TIMEOUT_S = 300
AGENT_SEARCH_K = 3
# One RTL answer spans a module row plus its blocks, so this lane needs more than the spec.
AGENT_RTL_SEARCH_K = 8
# Sentence-overlap chunking splits one spec passage across neighbours, so a section needs slots.
AGENT_SPEC_SEARCH_K = 8
# Sentence-overlap chunking emits near-identical neighbours, so over-fetch and drop them.
AGENT_SPEC_OVERFETCH = 3
AGENT_DEDUP_SHINGLE = 8
# Overlapping chunks may carry different rules; drop only fully covered content.
AGENT_DEDUP_MAX_OVERLAP = 1.0
AGENT_RTL_BLOCKS = 6
# Cross-encoders rate prose above code, so each lane's floor is calibrated per domain.
AGENT_LANE_SAMPLE = 3
LANE_CALIBRATION_PATH = OUTPUT_DIR / "lane_calibration.json"
LANE_CALIBRATION_SAMPLES = 200
LANE_CALIBRATION_QUANTILE = 0.95
LANE_FLOOR_FALLBACK = -6.0
# A lane below the floor keeps a small slice, so weak-but-real evidence stays inspectable.
AGENT_WEAK_LANE_SHARE = 0.25
AGENT_REVERSE_TERMS = 6
# Enable after the paired benchmark demonstrates a useful recall/cost tradeoff.
AGENT_QUERY_RECOVERY = False
AGENT_ABSENCE_TERMS = 6
AGENT_CONTEXT_CHARS = 12500
AGENT_SPEC_CONTEXT_CHARS = 4500
AGENT_RTL_CONTEXT_CHARS = 5500
AGENT_BRIDGE_CONTEXT_CHARS = 2500
# Large state machines are excerpted around their targets instead of crowding the lane.
AGENT_RTL_BLOCK_CHARS = 1200
AGENT_RTL_BLOCK_CONTEXT_LINES = 1

# A fixed sentinel lets the graph route insufficient evidence deterministically.
AGENT_INSUFFICIENT = "INSUFFICIENT"
