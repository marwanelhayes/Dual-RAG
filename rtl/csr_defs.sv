// =============================================================================
// csr_defs.sv
// -----------------------------------------------------------------------------
// CSR register mask definitions and interrupt-related constants for RISC-V.
//
// Responsibilities:
//   - Define write mask values for CSR registers (misa, mstatus, mcause, etc.)
//   - Define bit positions for interrupt enable and previous privilege state
//   - Define interrupt cause values for timer, software, and external interrupts
//   - Define MTVEC mode configurations (direct vs vectored)
// =============================================================================

// CSR register write masks
`define misa_mask   32'b0000_0000_1001_0101_0011_0001_1111_1111
`define mstatus_mask 32'b0000_0000_0000_0000_0000_0000_1000_1000
`define mcause_mask 32'b1000_0000_0000_0000_0000_1111_1111_1111
`define align_mask 32'b1111_1111_1111_1111_1111_1111_1111_1100
`define mtvec_mask 32'b1111_1111_1111_1111_1111_1111_1111_1101
`define mie_mask 32'b0000_0000_0000_0000_0000_1000_1000_1000
`define mpie_mask 32'b0000_0000_0000_0000_0000_1000_1000_1000

// MSTATUS bit positions
`define MIE 3     // Machine Interrupt Enable bit
`define MPIE 7    // Machine Interrupt Previous Enable bit
`define MPPS 11   // Machine Previous Privilege Start bit
`define MPPE 12   // Machine Previous Privilege End bit

// Interrupt enable bit positions in mie register
`define MT_I 7  // Machine Timer Interrupt Enable bit
`define MS_I 3  // Machine Software Interrupt Enable bit
`define ME_I 11 // Machine External Interrupt Enable bit

// Combined interrupt pending mask
`define IRQ (1 << `MT_I) | (1 << `MS_I) | (1 << `ME_I)

// MTVEC mode range
`define MTVEC_MODE_END 1
`define MTVEC_MODE_START 0

// MTVEC mode encoding
`define MTVEC_DIRECT 2'b00
`define MTVEC_VECTORED 2'b01

// Machine interrupt cause values
`define MACHINE_SOFTWARE_INTERRUPT_CAUSE 32'd3
`define MACHINE_TIMER_INTERRUPT_CAUSE 32'd7
`define MACHINE_EXTERNAL_INTERRUPT_CAUSE 32'd11
