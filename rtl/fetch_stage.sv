// =============================================================================
// fetch_stage.sv
// -----------------------------------------------------------------------------
// Instruction Fetch stage of the RISC-V 5-stage pipeline.
//
// Responsibilities:
//   - Fetch instructions from instruction memory using PC
//   - Compute next sequential PC (PC + 4)
//   - Handle pipeline stalls and flushes from hazard unit
//   - Output instruction and PC to decode stage
//
// Instantiates:
//   - pc_adder: calculates PC+4
//   - risc_instruction_memory: instruction ROM
//   - cache: L1 instruction cache
// =============================================================================
import shared_pkg::*;

module fetch_stage
#(
    parameter int  DATA_WIDTH         = 32,
    parameter int  ADDR_WIDTH         = 32,
    parameter int  CACHE_TOTAL_LINES  = 32,                          
    parameter int  CACHE_WAY          = 1,                          
    parameter int  CACHE_LINE_WORDS   = 4,                           
    parameter bit  CACHE_READ_ONLY    = 1'b1,                         
    parameter int  CACHE_AXI_SIZE     = 4
)
(
    // ── Clock and reset ───────────────────────────────────────────────────────
    input clk,
    input rst,

    // ── Program counter from previous stage ──────────────────────────────────
    input [ADDR_WIDTH-1:0] PCF,

    // ── Pipeline control from hazard unit ────────────────────────────────────
    input StallD,      // Stall the pipeline (hold current state)
    input FlushD,      // Flush the pipeline (inject NOP)

    // ── Outputs to decode stage ──────────────────────────────────────────────
    output logic [ADDR_WIDTH-1:0] PCPlus4D,      // PC+4 for current instruction
    output logic [DATA_WIDTH-1:0] InstructionD,  // Fetched instruction

    // ── Outputs to previous stage ────────────────────────────────────────────
    output wire [ADDR_WIDTH-1:0] PCPlus4F,        // PC+4 for next fetch address
    
    // ── AXI4 master interface: write address channel ─────────────────────────
    output logic [ADDR_WIDTH-1:0] MAWAddr,
    output logic [7:0]            MAWLen,
    output logic [CACHE_AXI_SIZE-1: 0]   MAWSize,
    output axi_burst_t            MAWBurst,
    output logic                  MAWValid,
    input  logic                  MAWReady,

    // ── AXI4 master interface: write data channel ────────────────────────────
    output logic [DATA_WIDTH-1:0] MWData,
    output logic [(DATA_WIDTH/8)-1:0] MWStrb,
    output logic                  MWLast,
    output logic                  MWValid,
    input  logic                  MWReady,

    // ── AXI4 master interface: write response channel ────────────────────────
    output logic                  MBReady,
    input  axi_resp_t             MBResp,
    input  logic                  MBValid,

    // ── AXI4 master interface: read address channel ──────────────────────────
    output logic [ADDR_WIDTH-1:0] MARAddr,
    output logic [7:0]            MARLen,
    output logic [CACHE_AXI_SIZE-1:0]   MARSize,
    output axi_burst_t            MARBurst,
    output logic                  MARValid,
    input  logic                  MARReady,

    // ── AXI4 master interface: read data channel ─────────────────────────────
    output logic                  MRReady,
    input  logic [DATA_WIDTH-1:0] MRRData,
    input  axi_resp_t             MRRResp,
    input  logic                  MRRLast,
    input  logic                  MRRValid,

    // ── Cache Response Signals ─────────────────────────
    output logic                 CacheHitF                     // Cache hit signal for stalling logic
);

    // ─── Internal wires – instruction fetch ───────────────────────────────────
    logic [DATA_WIDTH-1:0] InstructionF;  // Instruction from memory (combinational)

    // ─── fetch_stage: PC+4 calculation ───────────────────────────────────────
    pc_adder #(.ADDR_WIDTH(ADDR_WIDTH)) P1 
    (
        .PC(PCF),
        .PCPlus4(PCPlus4F)
    );

    // ─── fetch_stage: instruction cache ───────────────────────────────────────
    cache #(
        .DATA_WIDTH(DATA_WIDTH),
        .ADDR_WIDTH(ADDR_WIDTH),
        .TOTAL_LINES(CACHE_TOTAL_LINES),
        .WAY(CACHE_WAY),
        .LINE_WORDS(CACHE_LINE_WORDS),
        .READ_ONLY(CACHE_READ_ONLY),
        .AXI_SIZE(CACHE_AXI_SIZE)
    ) ICache (
                    
        .clk(clk),
        .rst(rst),
        .CPUAddr(PCF),
        .CPUWriteData('b0),
        .CPUWriteEn(1'b0),
        .CPUReadEn(1'b1),
        .CPUReadData(InstructionF),
        .CacheHit(CacheHitF),    
        .MAWAddr(MAWAddr),
        .MAWLen(MAWLen),
        .MAWSize(MAWSize),
        .MAWBurst(MAWBurst),
        .MAWValid(MAWValid),
        .MAWReady(MAWReady),
        .MWData(MWData),
        .MWStrb(MWStrb),
        .MWLast(MWLast),
        .MWValid(MWValid),
        .MWReady(MWReady),
        .MBReady(MBReady),
        .MBResp(MBResp),
        .MBValid(MBValid),
        .MARAddr(MARAddr),
        .MARLen(MARLen),
        .MARSize(MARSize),
        .MARBurst(MARBurst),
        .MARValid(MARValid),
        .MARReady(MARReady),
        .MRReady(MRReady),
        .MRRData(MRRData),
        .MRRResp(MRRResp),
        .MRRLast(MRRLast),
        .MRRValid(MRRValid)    
        );

    // ─── IF/ID pipeline register ─────────────────────────────────────────────
    always_ff @(posedge clk or negedge rst)
    begin
        if (!rst)
        begin
            PCPlus4D <= '0;
            InstructionD <= 32'h00_00_00_33;    // NOP instruction (ADDI x0, x0, 0)
        end
        else if(FlushD)
        begin
            PCPlus4D <= '0;
            InstructionD <= 32'h00_00_00_33;    // Flush with NOP
        end
        else if(!StallD)
        begin
            PCPlus4D <= PCPlus4F;                // Latch PC+4 on valid cycle
            InstructionD <= InstructionF;        // Latch fetched instruction
        end
    end

endmodule