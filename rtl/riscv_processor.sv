// =============================================================================
// riscv_processor.sv
// -----------------------------------------------------------------------------
// Top-level RISC-V 5-stage pipeline processor module.
//
// Responsibilities:
//   - Instantiate all five pipeline stages (Fetch, Decode, Execute, Memory, WB)
//   - Connect pipeline inter-stage signals
//   - Instantiate hazard detection and forwarding unit
//   - Manage external interrupt inputs
//   - Provide complete processor interface
//
// Instantiates:
//   - fetch_stage: instruction fetch
//   - decode_stage: instruction decode
//   - execute_stage: ALU and FPU execution
//   - memory_stage: data memory access
//   - wb_stage: write-back and PC update
//   - hazard_unit: hazard detection and forwarding
// =============================================================================
import shared_pkg::*;

module riscv_processor
#(
    parameter int   DATA_WIDTH = 32,
    parameter int   ADDR_WIDTH = 8,
    parameter int   ALU_SUB_CONTROL_WIDTH = 2,
    parameter int   STAGES = 4,
    parameter int   CACHE_TOTAL_LINES  = 32,                          
    parameter int   CACHE_WAY          = 1,                          
    parameter int   CACHE_LINE_WORDS   = 4,                           
    parameter bit   ICACHE_READ_ONLY    = 1'b1,
    parameter bit   DCACHE_READ_ONLY    = 1'b0,                         
    parameter int   CACHE_AXI_SIZE     = 4
)
(
    // ─── Clock and reset ───────────────────────────────────────────────────────
    input clk,
    input rst,

    // ─── External interrupt inputs ─────────────────────────────────────────────
    input ExternalInterrupt,    // External hardware interrupt
    input TimerInterrupt,       // Timer interrupt
    input SoftwareInterrupt,    // Software interrupt

    // ─── AXI4 master interface: ICache write address channel ─────────────────────────
    output logic [ADDR_WIDTH-1:0] IMAWAddr,
    output logic [7:0]            IMAWLen,
    output logic [CACHE_AXI_SIZE-1:0]   IMAWSize,
    output axi_burst_t            IMAWBurst,
    output logic                  IMAWValid,
    input  logic                  IMAWReady,

    // ── AXI4 master interface: ICachewrite data channel ────────────────────────────
    output logic [DATA_WIDTH-1:0] IMWData,
    output logic [(DATA_WIDTH/8)-1:0] IMWStrb,
    output logic                  IMWLast,
    output logic                  IMWValid,
    input  logic                  IMWReady,

    // ── AXI4 master interface: ICache write response channel ────────────────────────
    output logic                  IMBReady,
    input  axi_resp_t             IMBResp,
    input  logic                  IMBValid,

    // ── AXI4 master interface: ICache read address channel ──────────────────────────
    output logic [ADDR_WIDTH-1:0] IMARAddr,
    output logic [7:0]            IMARLen,
    output logic [CACHE_AXI_SIZE-1:0]   IMARSize,
    output axi_burst_t            IMARBurst,
    output logic                  IMARValid,
    input  logic                  IMARReady,

    // ── AXI4 master interface: ICache read data channel ─────────────────────────────
    output logic                  IMRReady,
    input  logic [DATA_WIDTH-1:0] IMRRData,
    input  axi_resp_t             IMRRResp,
    input  logic                  IMRRLast,
    input  logic                  IMRRValid,

    // ─── AXI4 master interface: DCache write address channel ─────────────────────────
    output logic [ADDR_WIDTH-1:0] DMAWAddr,
    output logic [7:0]            DMAWLen,
    output logic [CACHE_AXI_SIZE-1:0]   DMAWSize,
    output axi_burst_t            DMAWBurst,
    output logic                  DMAWValid,
    input  logic                  DMAWReady,

    // ── AXI4 master interface: DCache write data channel ────────────────────────────
    output logic [DATA_WIDTH-1:0] DMWData,
    output logic [(DATA_WIDTH/8)-1:0] DMWStrb,
    output logic                  DMWLast,
    output logic                  DMWValid,
    input  logic                  DMWReady,

    // ── AXI4 master interface: DCache write response channel ────────────────────────
    output logic                  DMBReady,
    input  axi_resp_t             DMBResp,
    input  logic                  DMBValid,

    // ── AXI4 master interface: DCache read address channel ──────────────────────────
    output logic [ADDR_WIDTH-1:0] DMARAddr,
    output logic [7:0]            DMARLen,
    output logic [CACHE_AXI_SIZE-1:0]   DMARSize,
    output axi_burst_t            DMARBurst,
    output logic                  DMARValid,
    input  logic                  DMARReady,

    // ── AXI4 master interface: DCache read data channel ─────────────────────────────
    output logic                  DMRReady,
    input  logic [DATA_WIDTH-1:0] DMRRData,
    input  axi_resp_t             DMRRResp,
    input  logic                  DMRRLast,
    input  logic                  DMRRValid
);

    // ─── IF stage interconnects ───────────────────────────────────────────────
    wire [ADDR_WIDTH-1:0] PCF;             // Program counter (fetch)
    wire [ADDR_WIDTH-1:0] PCPlus4F;        // PC + 4 (next sequential)
    wire CacheHitF;                     // Cache hit signal for stalling logic

    // ─── ID stage interconnects ───────────────────────────────────────────────
    wire [ADDR_WIDTH-1:0] PCPlus4D;        // PC+4 to decode
    wire [DATA_WIDTH-1:0] InstructionD;  // Fetched instruction
    wire StallD;                            // Stall decode stage
    wire FlushD;                            // Flush decode stage
    gpr_t Rs1D;                             // Source register 1 (decode)
    gpr_t Rs2D;                             // Source register 2 (decode)

    // ─── EX stage interconnects ───────────────────────────────────────────────
    wire signed [DATA_WIDTH-1:0] RD1E;     // GPR rs1 value
    wire signed [DATA_WIDTH-1:0] RD2E;     // GPR rs2 value
    wire signed [DATA_WIDTH-1:0] SignImmE; // Sign-extended immediate
    wire [ADDR_WIDTH-1:0] PCBranchE;      // Branch target address
    wire [ADDR_WIDTH-1:0] PCPlus4E;        // PC+4 to execute
    wire JumpE;                             // Jump instruction flag
    wire ALUSrcE;                           // ALU operand B select
    wire [2:0] ForwardAE;                  // Forwarding select A
    wire [2:0] ForwardBE;                  // Forwarding select B
    wire FlushE;                            // Flush execute stage
    wire RegWriteE;                        // Register write enable
    selector_t SelectorE;                  // Write-back select
    wire MemWriteE;                        // Memory write enable
    gpr_t Rs1E;                             // Source register 1 (execute)
    gpr_t Rs2E;                             // Source register 2 (execute)
    gpr_t RdE;                              // Destination register
    alu_operation_t ALUControlE;            // ALU operation select
    wire BranchE;                           // Branch instruction flag
    wire [2:0] funct3E;                     // funct3 field
    wire PCSrcE;                           // Branch/jump taken
    wire CsrAccessE;                       // CSR access enable
    csr_t CsrOperationE;                   // CSR operation type
    csr_index_t CsrIndexE;                 // CSR register index
    wire IllegaleInstructionE;              // Illegal instruction flag
    wire MRetE;                            // MRET instruction flag
    wire EcallE;                            // ECALL instruction flag
    wire EbreakE;                           // EBREAK instruction flag
    fpr_t RdFE;                             // FPR destination index
    wire [DATA_WIDTH-1:0] RD1FE;          // FPR rs1 value
    wire [DATA_WIDTH-1:0] RD2FE;          // FPR rs2 value
    fpu_operation_t FPUControlE;           // FPU operation type
    round_mode_t RoundModeE;               // Rounding mode
    wire FPURegWriteE;                     // FPR write enable
    move_operation_t MoveOperationE;        // FPU move operation
    fpr_t Rs1FE;                            // FPR source 1 index
    fpr_t Rs2FE;                            // FPR source 2 index
    wire [1:0] ForwardFloatingAE;          // FPU forwarding select A
    wire [1:0] ForwardFloatingBE;          // FPU forwarding select B
    wire FPUValidE;                        // Valid FPU operation

    // ─── MEM stage interconnects ──────────────────────────────────────────────
    wire signed [DATA_WIDTH-1:0] ALUOutM;    // ALU result
    wire signed [DATA_WIDTH-1:0] WriteDataM; // Data to store
    wire [2:0] funct3M;                        // funct3 field
    gpr_t RdM;                                // Destination register
    wire RegWriteM;                           // Register write enable
    wire [DATA_WIDTH-1:0] CsrOutM;           // CSR read data
    selector_t SelectorM;                     // Write-back select
    wire [ADDR_WIDTH-1:0] PCPlus4M;           // PC+4 to memory
    fpr_t RdFM;                               // FPR destination
    wire OverflowM;                           // FPU overflow flag
    wire UnderflowM;                          // FPU underflow flag
    wire NaNM;                               // FPU NaN flag
    wire InfM;                               // FPU infinity flag
    wire ZeroM;                              // FPU zero flag
    wire InvalidDivM;                         // Invalid division flag
    wire [DATA_WIDTH-1:0] FPUOutM;           // FPU result
    wire FPURegWriteM;                        // FPR write enable
    move_operation_t MoveOperationM;          // FPU move operation
    wire FPUBusyM;                            // FPU busy flag
    wire FPUDoneM;                            // FPU done flag
    wire CacheHitM;                           // Cache hit signal for stalling logic

    // ─── WB stage interconnects ───────────────────────────────────────────────
    wire signed [DATA_WIDTH-1:0] ReadDataW;    // Loaded data from memory
    wire signed [DATA_WIDTH-1:0] ALUOutW;     // ALU result to WB
    wire signed [DATA_WIDTH-1:0] ResultW;     // Final write-back result
    wire [DATA_WIDTH-1:0] CsrOutW;            // CSR data to WB
    gpr_t RdW;                                // Destination register
    selector_t SelectorW;                     // Write-back select
    wire RegWriteW;                           // Register write enable
    wire [ADDR_WIDTH-1:0] PCPlus4W;           // PC+4 to WB
    fpr_t RdFW;                               // FPR destination
    wire [DATA_WIDTH-1:0] FPUOutW;            // FPU result to WB
    move_operation_t MoveOperationW;          // FPU move operation
    wire FPURegWriteW;                        // FPR write enable

    // ─── Trap and control interconnects ──────────────────────────────────────
    wire TrapIsSet;                           // Trap pending flag
    wire [ADDR_WIDTH-1:0] CsrOutPC;           // Trap vector address

    // ─── riscv_processor: fetch_stage ─────────────────────────────────────────
    fetch_stage #(
        .DATA_WIDTH(DATA_WIDTH),
        .ADDR_WIDTH(ADDR_WIDTH),
        .CACHE_TOTAL_LINES(CACHE_TOTAL_LINES),
        .CACHE_WAY(CACHE_WAY),
        .CACHE_LINE_WORDS(CACHE_LINE_WORDS),
        .CACHE_READ_ONLY(ICACHE_READ_ONLY),
        .CACHE_AXI_SIZE(CACHE_AXI_SIZE)
    ) 
    Fetch
    (
        .clk(clk),
        .rst(rst),
        .PCF(PCF),
        .FlushD(FlushD),
        .StallD(StallD),
        .PCPlus4D(PCPlus4D),
        .InstructionD(InstructionD),
        .PCPlus4F(PCPlus4F),
        .CacheHitF(CacheHitF),
        .MAWAddr(IMAWAddr),
        .MAWLen(IMAWLen),
        .MAWSize(IMAWSize),
        .MAWBurst(IMAWBurst),
        .MAWValid(IMAWValid),
        .MAWReady(IMAWReady),
        .MWData(IMWData),
        .MWStrb(IMWStrb),
        .MWLast(IMWLast),
        .MWValid(IMWValid),
        .MWReady(IMWReady),
        .MBReady(IMBReady),
        .MBResp(IMBResp),
        .MBValid(IMBValid),
        .MARAddr(IMARAddr),
        .MARLen(IMARLen),
        .MARSize(IMARSize),
        .MARBurst(IMARBurst),
        .MARValid(IMARValid),
        .MARReady(IMARReady),
        .MRReady(IMRReady),
        .MRRData(IMRRData),
        .MRRResp(IMRRResp),
        .MRRLast(IMRRLast),
        .MRRValid(IMRRValid)
    );

    // ─── riscv_processor: decode_stage ─────────────────────────────────────────
    decode_stage #(.DATA_WIDTH(DATA_WIDTH),.ADDR_WIDTH(ADDR_WIDTH),.ALU_SUB_CONTROL_WIDTH(ALU_SUB_CONTROL_WIDTH)) 
    Decode
    (
        .clk(clk),
        .rst(rst),
        .PCPlus4D(PCPlus4D),
        .InstructionD(InstructionD),
        .FlushE(FlushE),
        .RegWriteW(RegWriteW),
        .RdW(RdW),
        .BranchE(BranchE),
        .funct3E(funct3E),
        .ResultW(ResultW),
        .Rs1E(Rs1E),
        .Rs2E(Rs2E),
        .Rs1D(Rs1D),
        .Rs2D(Rs2D),
        .RdE(RdE),
        .ALUControlE(ALUControlE),
        .RD1E(RD1E),
        .RD2E(RD2E),
        .CsrIndexE(CsrIndexE),
        .SignImmE(SignImmE),
        .RegWriteE(RegWriteE),
        .SelectorE(SelectorE),
        .MemWriteE(MemWriteE),
        .PCBranchE(PCBranchE),
        .PCPlus4E(PCPlus4E),
        .JumpE(JumpE),
        .CsrOperationE(CsrOperationE),
        .CsrAccessE(CsrAccessE),
        .ALUSrcE(ALUSrcE),
        .MRetE(MRetE),
        .EcallE(EcallE),
        .EbreakE(EbreakE),
        .IllegaleInstructionE(IllegaleInstructionE),
        .RdFW(RdFW),
        .FPUOutW(FPUOutW),
        .MoveOperationW(MoveOperationW),
        .FPURegWriteW(FPURegWriteW),
        .RdFE(RdFE),
        .RD1FE(RD1FE),
        .RD2FE(RD2FE),
        .FPUControlE(FPUControlE),
        .RoundModeE(RoundModeE),
        .FPURegWriteE(FPURegWriteE),
        .MoveOperationE(MoveOperationE),
        .Rs1FE(Rs1FE),
        .Rs2FE(Rs2FE),
        .FPUValidE(FPUValidE)
    );

    // ─── riscv_processor: execute_stage ───────────────────────────────────────
    execute_stage #(.DATA_WIDTH(DATA_WIDTH),.ADDR_WIDTH(ADDR_WIDTH),.ALU_SUB_CONTROL_WIDTH(ALU_SUB_CONTROL_WIDTH),.STAGES(STAGES)) 
    Execute
    (
        .clk(clk),
        .rst(rst),
        .RD1E(RD1E),
        .RD2E(RD2E),
        .SignImmE(SignImmE),
        .ResultW(ResultW),
        .ALUControlE(ALUControlE),
        .funct3E(funct3E),
        .BranchE(BranchE),
        .PCPlus4E(PCPlus4E),
        .PCSrcE(PCSrcE),
        .JumpE(JumpE),
        .ForwardAE(ForwardAE),
        .ForwardBE(ForwardBE),
        .Rs1E(Rs1E),
        .RdE(RdE),
        .ALUSrcE(ALUSrcE),
        .CsrAccessE(CsrAccessE),
        .MRetE(MRetE),
        .EcallE(EcallE),
        .EbreakE(EbreakE),
        .ExternalInterrupt(ExternalInterrupt),
        .TimerInterrupt(TimerInterrupt),
        .SoftwareInterrupt(SoftwareInterrupt),
        .CsrOutPC(CsrOutPC),
        .TrapIsSet(TrapIsSet),
        .IllegaleInstructionE(IllegaleInstructionE),
        .CsrOperationE(CsrOperationE),
        .funct3M(funct3M),
        .RegWriteE(RegWriteE),
        .CsrIndexE(CsrIndexE),
        .SelectorE(SelectorE),
        .MemWriteE(MemWriteE),
        .ALUOutM(ALUOutM),
        .WriteDataM(WriteDataM),
        .RdM(RdM),
        .PCPlus4M(PCPlus4M),
        .CsrOutM(CsrOutM),
        .RegWriteM(RegWriteM),
        .SelectorM(SelectorM),
        .MemWriteM(MemWriteM),
        .FPUOutW(FPUOutW),
        .RdFE(RdFE),
        .RD1FE(RD1FE),
        .RD2FE(RD2FE),
        .FPUControlE(FPUControlE),
        .RoundModeE(RoundModeE),
        .MoveOperationE(MoveOperationE),
        .FPURegWriteE(FPURegWriteE),
        .RdFM(RdFM),
        .OverflowM(OverflowM),
        .UnderflowM(UnderflowM),
        .NaNM(NaNM),
        .InfM(InfM),
        .ZeroM(ZeroM),
        .InvalidDivM(InvalidDivM),
        .FPUOutM(FPUOutM),
        .FPURegWriteM(FPURegWriteM),
        .MoveOperationM(MoveOperationM),
        .Rs1FE(Rs1FE),
        .Rs2FE(Rs2FE),
        .ForwardFloatingAE(ForwardFloatingAE),
        .ForwardFloatingBE(ForwardFloatingBE),
        .FPUValidE(FPUValidE),
        .FPUBusyM(FPUBusyM),
        .FPUDoneM(FPUDoneM)
    );

    // ─── riscv_processor: memory_stage ─────────────────────────────────────────
    memory_stage #(.DATA_WIDTH(DATA_WIDTH),.ADDR_WIDTH(ADDR_WIDTH)) 
    Memory
    (
        .clk(clk),
        .rst(rst),
        .ALUOutM(ALUOutM),
        .funct3M(funct3M),
        .WriteDataM(WriteDataM),
        .RdM(RdM),
        .CsrOutM(CsrOutM),
        .PCPlus4M(PCPlus4M),
        .RegWriteW(RegWriteW),
        .RegWriteM(RegWriteM),
        .SelectorM(SelectorM),
        .MemWriteM(MemWriteM),
        .CacheHitM(CacheHitM),
        .ReadDataW(ReadDataW),
        .RdW(RdW),
        .PCPlus4W(PCPlus4W),
        .SelectorW(SelectorW),
        .CsrOutW(CsrOutW),
        .ALUOutW(ALUOutW),
        .RdFM(RdFM),
        .OverflowM(OverflowM),
        .UnderflowM(UnderflowM),
        .NaNM(NaNM),
        .InfM(InfM),
        .ZeroM(ZeroM),
        .InvalidDivM(InvalidDivM),
        .FPUOutM(FPUOutM),
        .FPURegWriteM(FPURegWriteM),
        .MoveOperationM(MoveOperationM),
        .RdFW(RdFW),
        .OverflowW(OverflowW),
        .UnderflowW(UnderflowW),
        .NaNW(NaNW),
        .InfW(InfW),
        .ZeroW(ZeroW),
        .InvalidDivW(InvalidDivW),
        .FPUOutW(FPUOutW),
        .FPURegWriteW(FPURegWriteW),
        .MoveOperationW(MoveOperationW),
        .MAWAddr(DMAWAddr),
        .MAWLen(DMAWLen),
        .MAWSize(DMAWSize),
        .MAWBurst(DMAWBurst),
        .MAWValid(DMAWValid),
        .MAWReady(DMAWReady),
        .MWData(DMWData),
        .MWStrb(DMWStrb),
        .MWLast(DMWLast),
        .MWValid(DMWValid),
        .MWReady(DMWReady),
        .MBReady(DMBReady),
        .MBResp(DMBResp),
        .MBValid(DMBValid),
        .MARAddr(DMARAddr),
        .MARLen(DMARLen),
        .MARSize(DMARSize),
        .MARBurst(DMARBurst),
        .MARValid(DMARValid),
        .MARReady(DMARReady),
        .MRReady(DMRReady),
        .MRRData(DMRRData),
        .MRRResp(DMRRResp),
        .MRRLast(DMRRLast),
        .MRRValid(DMRRValid)
    );

    wb_stage #(.DATA_WIDTH(DATA_WIDTH),.ADDR_WIDTH(ADDR_WIDTH)) 
    WriteBack
    (
        .clk(clk),
        .rst(rst),
        .PCSrcE(PCSrcE),
        .StallF(StallF),
        .PCPlus4W(PCPlus4W),
        .PCPlus4F(PCPlus4F),
        .CsrOutW(CsrOutW),
        .PCBranchE(PCBranchE),
        .ALUOutW(ALUOutW),
        .ReadDataW(ReadDataW),
        .CsrOutPC(CsrOutPC),
        .TrapIsSet(TrapIsSet),
        .SelectorW(SelectorW),
        .ResultW(ResultW),
        .PCF(PCF) 
    );

    hazard_unit Hazard
    (
        .Rs1E(Rs1E),
        .Rs2E(Rs2E),
        .RdE(RdE),
        .Rs1D(Rs1D), 
        .Rs2D(Rs2D), 
        .RdM(RdM),
        .RdW(RdW),
        .ICacheHit(CacheHitF),
        .DCacheHit(CacheHitM),
        .RegWriteM(RegWriteM),
        .RegWriteW(RegWriteW),
        .PCSrcE(PCSrcE),
        .TrapIsSet(TrapIsSet),
        .ForwardAE(ForwardAE),
        .ForwardBE(ForwardBE),
        .StallD(StallD),
        .StallF(StallF),
        .FlushE(FlushE),
        .SelectorE(SelectorE),
        .FlushD(FlushD),
        .ForwardFloatingAE(ForwardFloatingAE),
        .ForwardFloatingBE(ForwardFloatingBE),
        .MoveOperationE(MoveOperationE),
        .RdFM(RdFM),
        .RdFW(RdFW),
        .Rs1FE(Rs1FE),
        .Rs2FE(Rs2FE),
        .FPURegWriteM(FPURegWriteM),
        .FPURegWriteW(FPURegWriteW),
        .FPUValidE(FPUValidE),
        .FPUBusyM(FPUBusyM)
    );

endmodule