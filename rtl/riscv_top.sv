// =============================================================================
// riscv_top.sv
// -----------------------------------------------------------------------------
// Closed top level: the RISC-V core with its instruction and data memories
// attached, so the design forms a single hierarchy rooted here.
//
// riscv_processor is the core, but it is not a closed design: it leaves two AXI4
// master interfaces (IM* for the instruction cache, DM* for the data cache)
// hanging at its boundary. Anything walking the design from riscv_processor
// therefore sees a tree with two open edges. This module closes them against the
// two memories, which are AXI4 slaves, leaving only clk, rst and the three
// interrupt lines at the boundary.
//
// It contains no logic of its own. Every line below is either a parameter, an
// interconnect wire, or a port connection - if this file ever needs an
// assignment, something is wrong with the assumption that the core and the
// memories already agree on a protocol.
//
// Instantiates:
//   - riscv_processor:          the 5-stage core, AXI4 manager on both caches
//   - risc_instruction_memory:  AXI4 subordinate, serves the instruction cache
//   - risc_data_memory:         AXI4 subordinate, serves the data cache
// =============================================================================
import shared_pkg::*;

module riscv_top
#(
    // ADDR_WIDTH is taken from riscv_processor's own default rather than from the
    // memories' (32), because the core is what drives the address channels. The
    // memories size themselves from it - DEPTH = 2**(ADDR_WIDTH-2) - so passing
    // one value to all three is what keeps the AXI ports the same width on both
    // ends of every connection
    parameter int DATA_WIDTH = 32,
    parameter int ADDR_WIDTH = 8,
    parameter int AXI_SIZE   = 4
)
(
    // ─── Clock and reset ───────────────────────────────────────────────────────
    input logic clk,
    input logic rst,

    // ─── External interrupt inputs ─────────────────────────────────────────────
    input logic ExternalInterrupt,
    input logic TimerInterrupt,
    input logic SoftwareInterrupt
);

    // ─── ICache AXI4 interconnect: core manager  <->  instruction memory ───────
    logic [ADDR_WIDTH-1:0]     IMAWAddr;
    logic [7:0]                IMAWLen;
    logic [AXI_SIZE-1:0]       IMAWSize;
    axi_burst_t                IMAWBurst;
    logic                      IMAWValid;
    logic                      IMAWReady;

    logic [DATA_WIDTH-1:0]     IMWData;
    logic [(DATA_WIDTH/8)-1:0] IMWStrb;
    logic                      IMWLast;
    logic                      IMWValid;
    logic                      IMWReady;

    logic                      IMBReady;
    axi_resp_t                 IMBResp;
    logic                      IMBValid;

    logic [ADDR_WIDTH-1:0]     IMARAddr;
    logic [7:0]                IMARLen;
    logic [AXI_SIZE-1:0]       IMARSize;
    axi_burst_t                IMARBurst;
    logic                      IMARValid;
    logic                      IMARReady;

    logic                      IMRReady;
    logic [DATA_WIDTH-1:0]     IMRRData;
    axi_resp_t                 IMRRResp;
    logic                      IMRRLast;
    logic                      IMRRValid;

    // ─── DCache AXI4 interconnect: core manager  <->  data memory ─────────────
    logic [ADDR_WIDTH-1:0]     DMAWAddr;
    logic [7:0]                DMAWLen;
    logic [AXI_SIZE-1:0]       DMAWSize;
    axi_burst_t                DMAWBurst;
    logic                      DMAWValid;
    logic                      DMAWReady;

    logic [DATA_WIDTH-1:0]     DMWData;
    logic [(DATA_WIDTH/8)-1:0] DMWStrb;
    logic                      DMWLast;
    logic                      DMWValid;
    logic                      DMWReady;

    logic                      DMBReady;
    axi_resp_t                 DMBResp;
    logic                      DMBValid;

    logic [ADDR_WIDTH-1:0]     DMARAddr;
    logic [7:0]                DMARLen;
    logic [AXI_SIZE-1:0]       DMARSize;
    axi_burst_t                DMARBurst;
    logic                      DMARValid;
    logic                      DMARReady;

    logic                      DMRReady;
    logic [DATA_WIDTH-1:0]     DMRRData;
    axi_resp_t                 DMRRResp;
    logic                      DMRRLast;
    logic                      DMRRValid;

    // ─── The core ─────────────────────────────────────────────────────────────
    riscv_processor #(
        .DATA_WIDTH     (DATA_WIDTH),
        .ADDR_WIDTH     (ADDR_WIDTH),
        .CACHE_AXI_SIZE (AXI_SIZE)
    ) u_core (
        .clk               (clk),
        .rst               (rst),

        .ExternalInterrupt (ExternalInterrupt),
        .TimerInterrupt    (TimerInterrupt),
        .SoftwareInterrupt (SoftwareInterrupt),

        .IMAWAddr  (IMAWAddr),  .IMAWLen   (IMAWLen),   .IMAWSize  (IMAWSize),
        .IMAWBurst (IMAWBurst), .IMAWValid (IMAWValid), .IMAWReady (IMAWReady),
        .IMWData   (IMWData),   .IMWStrb   (IMWStrb),   .IMWLast   (IMWLast),
        .IMWValid  (IMWValid),  .IMWReady  (IMWReady),
        .IMBReady  (IMBReady),  .IMBResp   (IMBResp),   .IMBValid  (IMBValid),
        .IMARAddr  (IMARAddr),  .IMARLen   (IMARLen),   .IMARSize  (IMARSize),
        .IMARBurst (IMARBurst), .IMARValid (IMARValid), .IMARReady (IMARReady),
        .IMRReady  (IMRReady),  .IMRRData  (IMRRData),  .IMRRResp  (IMRRResp),
        .IMRRLast  (IMRRLast),  .IMRRValid (IMRRValid),

        .DMAWAddr  (DMAWAddr),  .DMAWLen   (DMAWLen),   .DMAWSize  (DMAWSize),
        .DMAWBurst (DMAWBurst), .DMAWValid (DMAWValid), .DMAWReady (DMAWReady),
        .DMWData   (DMWData),   .DMWStrb   (DMWStrb),   .DMWLast   (DMWLast),
        .DMWValid  (DMWValid),  .DMWReady  (DMWReady),
        .DMBReady  (DMBReady),  .DMBResp   (DMBResp),   .DMBValid  (DMBValid),
        .DMARAddr  (DMARAddr),  .DMARLen   (DMARLen),   .DMARSize  (DMARSize),
        .DMARBurst (DMARBurst), .DMARValid (DMARValid), .DMARReady (DMARReady),
        .DMRReady  (DMRReady),  .DMRRData  (DMRRData),  .DMRRResp  (DMRRResp),
        .DMRRLast  (DMRRLast),  .DMRRValid (DMRRValid)
    );

    // ─── Instruction memory: AXI4 subordinate on the ICache port ──────────────
    risc_instruction_memory #(
        .DATA_WIDTH (DATA_WIDTH),
        .ADDR_WIDTH (ADDR_WIDTH),
        .AXI_SIZE   (AXI_SIZE)
    ) u_imem (
        .clk      (clk),
        .rst      (rst),

        .MAWAddr  (IMAWAddr),  .MAWLen   (IMAWLen),   .MAWSize  (IMAWSize),
        .MAWBurst (IMAWBurst), .MAWValid (IMAWValid), .MAWReady (IMAWReady),
        .MWData   (IMWData),   .MWStrb   (IMWStrb),   .MWLast   (IMWLast),
        .MWValid  (IMWValid),  .MWReady  (IMWReady),
        .MBReady  (IMBReady),  .MBResp   (IMBResp),   .MBValid  (IMBValid),
        .MARAddr  (IMARAddr),  .MARLen   (IMARLen),   .MARSize  (IMARSize),
        .MARBurst (IMARBurst), .MARValid (IMARValid), .MARReady (IMARReady),
        .MRReady  (IMRReady),  .MRRData  (IMRRData),  .MRRResp  (IMRRResp),
        .MRRLast  (IMRRLast),  .MRRValid (IMRRValid)
    );

    // ─── Data memory: AXI4 subordinate on the DCache port ─────────────────────
    risc_data_memory #(
        .DATA_WIDTH (DATA_WIDTH),
        .ADDR_WIDTH (ADDR_WIDTH),
        .AXI_SIZE   (AXI_SIZE)
    ) u_dmem (
        .clk      (clk),
        .rst      (rst),

        .MAWAddr  (DMAWAddr),  .MAWLen   (DMAWLen),   .MAWSize  (DMAWSize),
        .MAWBurst (DMAWBurst), .MAWValid (DMAWValid), .MAWReady (DMAWReady),
        .MWData   (DMWData),   .MWStrb   (DMWStrb),   .MWLast   (DMWLast),
        .MWValid  (DMWValid),  .MWReady  (DMWReady),
        .MBReady  (DMBReady),  .MBResp   (DMBResp),   .MBValid  (DMBValid),
        .MARAddr  (DMARAddr),  .MARLen   (DMARLen),   .MARSize  (DMARSize),
        .MARBurst (DMARBurst), .MARValid (DMARValid), .MARReady (DMARReady),
        .MRReady  (DMRReady),  .MRRData  (DMRRData),  .MRRResp  (DMRRResp),
        .MRRLast  (DMRRLast),  .MRRValid (DMRRValid)
    );

endmodule
