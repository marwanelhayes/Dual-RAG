// =============================================================================
// risc_reg_file.sv
// -----------------------------------------------------------------------------
// General Purpose Register (GPR) File for RISC-V processor.
//
// Responsibilities:
//   - Provide dual-ported read access (two simultaneous reads)
//   - Provide single-ported write access
//   - Store 32 integer registers (x0-x31)
//   - x0 is hardwired to zero
//
// Instantiates:
//   - risc_mem: four byte-lane memory instances for 32-bit registers
// =============================================================================
import shared_pkg::*;

module risc_reg_file
#(
    parameter int DATA_WIDTH = 32,
    parameter int ADDR_WIDTH = 5
) 
(
    // ─── Clock and reset ───────────────────────────────────────────────────────
    input clk,
    input rst,

    // ─── Read/Write interface ─────────────────────────────────────────────────
    input gpr_t Rs1,Rs2,Rd,            // Source and destination register indices
    input signed [DATA_WIDTH-1:0] WData,   // Write data
    input WE,                          // Write enable

    // ─── Read data outputs ───────────────────────────────────────────────────
    output logic signed [DATA_WIDTH-1:0] RD1,   // Read data port 1
    output logic signed [DATA_WIDTH-1:0] RD2    // Read data port 2
);
    localparam int WIDTH_MEM = (DATA_WIDTH/4);    // 4 byte lanes

    // ─── risc_reg_file: byte lane 0 (bits 7:0) ───────────────────────────────
    risc_mem  #(.DATA_WIDTH(WIDTH_MEM),.ADDR_WIDTH(ADDR_WIDTH)) M1 
    (
      .clk(clk),
      .rst(rst),
      .Rs1(Rs1),
      .Rs2(Rs2),
      .Rd(Rd),
      .WData(WData[WIDTH_MEM-1:0]),
      .WE(WE),
      .RD1(RD1[WIDTH_MEM-1:0]),
      .RD2(RD2[WIDTH_MEM-1:0])  
    );

    risc_mem  #(.DATA_WIDTH(WIDTH_MEM),.ADDR_WIDTH(ADDR_WIDTH)) M2 
    (
      .clk(clk),
      .rst(rst),
      .Rs1(Rs1),
      .Rs2(Rs2),
      .Rd(Rd),
      .WData(WData[2*WIDTH_MEM-1:WIDTH_MEM]),
      .WE(WE),
      .RD1(RD1[2*WIDTH_MEM-1:WIDTH_MEM]),
      .RD2(RD2[2*WIDTH_MEM-1:WIDTH_MEM])  
    );

    risc_mem  #(.DATA_WIDTH(WIDTH_MEM),.ADDR_WIDTH(ADDR_WIDTH)) M3 
    (
      .clk(clk),
      .rst(rst),
      .Rs1(Rs1),
      .Rs2(Rs2),
      .Rd(Rd),
      .WData(WData[3*WIDTH_MEM-1:2*WIDTH_MEM]),
      .WE(WE),
      .RD1(RD1[3*WIDTH_MEM-1:2*WIDTH_MEM]),
      .RD2(RD2[3*WIDTH_MEM-1:2*WIDTH_MEM])
    );

    risc_mem  #(.DATA_WIDTH(WIDTH_MEM),.ADDR_WIDTH(ADDR_WIDTH)) M4 
    (
      .clk(clk),
      .rst(rst),
      .Rs1(Rs1),
      .Rs2(Rs2),
      .Rd(Rd),
      .WData(WData[4*WIDTH_MEM-1:3*WIDTH_MEM]),
      .WE(WE),
      .RD1(RD1[4*WIDTH_MEM-1:3*WIDTH_MEM]),
      .RD2(RD2[4*WIDTH_MEM-1:3*WIDTH_MEM])
    );


endmodule