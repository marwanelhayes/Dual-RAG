// =============================================================================
// csr_file.sv
// -----------------------------------------------------------------------------
// Control and Status Register (CSR) file for RISC-V processor.
//
// Responsibilities:
//   - Manage machine-mode CSRs (mstatus, mie, mtvec, mepc, mcause, etc.)
//   - Handle trap entry and return (MRET)
//   - Process CSR read/write operations (CSRRW, CSRRS, CSRRC, etc.)
//   - Manage interrupt detection and pending flags
//   - Provide rounding mode for FPU operations
// =============================================================================
import shared_pkg::*;
`include "csr_defs.sv"

module csr_file
#(
    parameter int DATA_WIDTH = 32,
    parameter int ADDR_WIDTH = 32
) 
(
    // ─── Clock and reset ───────────────────────────────────────────────────────
    input clk,
    input rst,

    // ─── CSR operation interface ─────────────────────────────────────────────
    input csr_t CsrOperation,             // CSR operation type
    input gpr_t Rs,                       // Source register for CSR ops
    input traps_t Traps,                  // Trap cause code
    input mret,                           // MRET instruction flag
    input [ADDR_WIDTH-1:0] PC,            // Current PC (for mepc)
    input [ADDR_WIDTH-1:0] Address,       // Fault address (for mbadaddr)
    input CsrAccess,                      // CSR access enable
    input [DATA_WIDTH-1:0] CsrIn,        // CSR write data

    // ─── Interrupt inputs ─────────────────────────────────────────────────────
    input TimerInterrupt,                 // Timer interrupt
    input ExternalInterrupt,              // External hardware interrupt
    input SoftwareInterrupt,              // Software interrupt

    // ─── CSR register index ───────────────────────────────────────────────────
    input csr_index_t CsrIndex,           // CSR address
    
    // ─── Outputs ───────────────────────────────────────────────────────────────
    output logic [ADDR_WIDTH-1:0] CsrOutPC,   // Trap return vector
    output logic [DATA_WIDTH-1:0] CsrOut,     // CSR read data
    output logic TrapIsSet,                   // Trap pending flag
    output round_mode_t RoundingMode          // FPU rounding mode
);

    (* ram_style = "block" *) logic [DATA_WIDTH-1:0] CsrFile [4096]; // CSR file with 4096 entries, each 32 bits wide
    localparam logic [DATA_WIDTH-1:0] AlignMask = `align_mask;
    wire InterruptSet;
    wire TimerInterruptPending;
    wire ExternalInterruptPending;
    wire SoftwareInterruptPending;
    wire TimerInterruptEnabled;
    wire ExternalInterruptEnabled;
    wire SoftwareInterruptEnabled;
    wire TimerInterruptSet;
    wire ExternalInterruptSet;
    wire SoftwareInterruptSet;
    wire TrapsNotZero;
    wire RsNotZero;

    assign TimerInterruptPending = TimerInterrupt | CsrFile[mip][`MT_I];
    assign ExternalInterruptPending = ExternalInterrupt | CsrFile[mip][`ME_I];
    assign SoftwareInterruptPending = SoftwareInterrupt | CsrFile[mip][`MS_I];

    assign TimerInterruptEnabled = CsrFile[mie][`MT_I];
    assign ExternalInterruptEnabled = CsrFile[mie][`ME_I];
    assign SoftwareInterruptEnabled = CsrFile[mie][`MS_I];

    assign TimerInterruptSet = CsrFile[mstatus][`MIE] & TimerInterruptEnabled & TimerInterruptPending;
    assign ExternalInterruptSet = CsrFile[mstatus][`MIE] & ExternalInterruptEnabled & ExternalInterruptPending;
    assign SoftwareInterruptSet = CsrFile[mstatus][`MIE] & SoftwareInterruptEnabled & SoftwareInterruptPending;

    assign InterruptSet = ExternalInterruptSet | SoftwareInterruptSet | TimerInterruptSet;

    assign TrapsNotZero = |(Traps);
    assign RsNotZero = |int'(Rs);

    function void ResetCsrFile;
        CsrFile[mstatus] <= 0;
        CsrFile[misa] <= 32'h40_00_01_00;
        CsrFile[medeleg] <= 0;
        CsrFile[mideleg] <= 0;
        CsrFile[mie] <= 0;
        CsrFile[mtvec] <= 0;
        CsrFile[mscratch] <= 0;
        CsrFile[mepc] <= 0;
        CsrFile[mcause] <= 0;
        CsrFile[mbadaddr] <= 0;
        CsrFile[mip] <= 0;
        CsrFile[mcycle] <= 0;
        CsrFile[mhartid] <= 0;
        CsrFile[mvendorid] <= 32'h48_41_4E_4F;
        CsrFile[fflags] <= 0;
        CsrFile[frm] <= 0;
        CsrFile[fcsr] <= 0;
        CsrOut <= 0;
        CsrOutPC <= 0;
        TrapIsSet <= 1'b0;
    endfunction:ResetCsrFile

    function automatic logic [DATA_WIDTH-1:0] TrapCause(input traps_t TrapCode);
        case(TrapCode)
            InstructionAddressMisalignedOrUserSoftwareInterrupt: TrapCause = 32'd0;
            InstructionAccessFaultOrSupervisorSoftwareInterrupt: TrapCause = 32'd1;
            IllegalInstructionOrHypervisorSoftwareInterrupt: TrapCause = 32'd2;
            BreakpointOrMachineSoftwareInterrupt: TrapCause = 32'd3;
            LoadAddressMisalignedOrUserSoftwareInterrupt: TrapCause = 32'd4;
            LoadAddressFaultOrSupervisorTimerInterrupt: TrapCause = 32'd5;
            StoreAddressMisalignedOrHyperVisorTimerInterrupt: TrapCause = 32'd6;
            StoreAddressFaultOrMachineTimerInterrupt: TrapCause = 32'd7;
            EcallUOrUserExternalInterrupt: TrapCause = 32'd8;
            EcallSOrSupervisorExternalInterrupt: TrapCause = 32'd9;
            EcallHOrHypervisorExternalInterrupt: TrapCause = 32'd10;
            EcallMOrMachineExternalInterrupt: TrapCause = 32'd11;
            default: TrapCause = 32'd0;
        endcase
    endfunction:TrapCause

    function automatic logic [ADDR_WIDTH-1:0] TrapVector(input logic Interrupt, input logic [DATA_WIDTH-1:0] Cause);
        TrapVector = {CsrFile[mtvec][ADDR_WIDTH-1:2], 2'b00};
        if(Interrupt && (CsrFile[mtvec][`MTVEC_MODE_END:`MTVEC_MODE_START] == `MTVEC_VECTORED))
            TrapVector = {CsrFile[mtvec][ADDR_WIDTH-1:2], 2'b00} + (Cause[ADDR_WIDTH-1:0] << 2);
    endfunction:TrapVector

    function void HandleTrapEntry(input logic [DATA_WIDTH-1:0] Cause, input logic Interrupt);
        CsrFile[mepc] <= PC & AlignMask;
        TrapIsSet <= 1'b1;
        CsrFile[mstatus][`MPIE] <= CsrFile[mstatus][`MIE];
        CsrFile[mstatus][`MIE] <= 1'b0;
        CsrFile[mcause] <= {Interrupt, Cause[DATA_WIDTH-2:0]};
        CsrOutPC <= TrapVector(Interrupt, Cause);
    endfunction:HandleTrapEntry

    function void HandleException;
        HandleTrapEntry(TrapCause(Traps), 1'b0);
        CsrFile[mbadaddr] <= Address;
    endfunction:HandleException

    function void HandleExternalInterrupt;
        HandleTrapEntry(`MACHINE_EXTERNAL_INTERRUPT_CAUSE, 1'b1);
        CsrFile[mip][`ME_I] <= ExternalInterrupt;
    endfunction:HandleExternalInterrupt

    function void HandleSoftwareInterrupt;
        HandleTrapEntry(`MACHINE_SOFTWARE_INTERRUPT_CAUSE, 1'b1);
        CsrFile[mip][`MS_I] <= SoftwareInterrupt;
    endfunction:HandleSoftwareInterrupt

    function void HandleTimerInterrupt;
        HandleTrapEntry(`MACHINE_TIMER_INTERRUPT_CAUSE, 1'b1);
        CsrFile[mip][`MT_I] <= TimerInterrupt;
    endfunction:HandleTimerInterrupt

    function void HandleReturn;
        CsrOutPC <= CsrFile[mepc][ADDR_WIDTH-1:0] & AlignMask;
        CsrFile[mstatus][`MIE] <= CsrFile[mstatus][`MPIE];
        CsrFile[mstatus][`MPIE] <= 1'b1;
        TrapIsSet <= 1'b0;
    endfunction:HandleReturn

    // Index must hold the full 12-bit CSR address (csr_index_t). Using
    // ADDR_WIDTH (the 10-bit data-address width) here truncated high CSR
    // addresses (mhartid/mcycle/...), so writes landed in the wrong slot
    // while the read side used the full CsrIndex - register never updated.
    function void UpdateCsr(input logic [DATA_WIDTH-1:0] X, input csr_index_t Index);
        if(Index == mstatus)
            CsrFile[Index] <= X & `mstatus_mask;
        else if(Index == mtvec)
            CsrFile[Index] <= X & `mtvec_mask;
        else if(Index == mepc)
            CsrFile[Index] <= X & `align_mask;
        else if(Index == mscratch)
            CsrFile[Index] <= X;
        else if(Index == mie)
            CsrFile[Index] <= X & `mie_mask;
        else if(Index == mcause)
            CsrFile[Index] <= X & `mcause_mask;
        else if (Index == fflags)
        begin
            CsrFile[Index][4:0] <= X;
            CsrFile[fcsr][4:0] <= X;
        end
        else if( Index == frm)
        begin
            CsrFile[Index][2:0] <= X;
            CsrFile[fcsr][7:5] <= X;
        end
        else if( Index == fcsr)
        begin
            CsrFile[Index][7:0] <= X;
            CsrFile[fflags][4:0] <= X;
            CsrFile[frm][2:0] <= X[7:5];
        end
        else if(Index != misa && Index != mvendorid && Index != mip)
            CsrFile[Index] <= X;
    endfunction:UpdateCsr

    function void UpdateInterruptPending;
        CsrFile[mip][`MT_I] <= TimerInterrupt;
        CsrFile[mip][`ME_I] <= ExternalInterrupt;
        CsrFile[mip][`MS_I] <= SoftwareInterrupt;
    endfunction:UpdateInterruptPending

    initial 
    begin
        foreach (CsrFile[i]) 
        begin
            CsrFile[i] = 0;
        end
    end

    always_ff @(posedge clk or negedge rst) 
    begin:CSR_File
        if (!rst) 
        begin:Reset
            ResetCsrFile();
        end:Reset
        else
        begin 
            UpdateInterruptPending();
            if (TrapsNotZero)
            begin:Exception_Handling
                HandleException();
            end:Exception_Handling
            else if(mret)
            begin:Return_From_Trap
                HandleReturn();
            end:Return_From_Trap
            else if(InterruptSet)
            begin:Interrupt_Handling
                if(ExternalInterruptSet)
                begin:External_Interrupt
                    HandleExternalInterrupt();
                end:External_Interrupt
                else if(SoftwareInterruptSet)
                begin:Software_Interrupt
                    HandleSoftwareInterrupt();
                end:Software_Interrupt
                else if(TimerInterruptSet)
                begin:Timer_Interrupt
                    HandleTimerInterrupt();
                end:Timer_Interrupt
            end:Interrupt_Handling
            else if (CsrAccess) 
            begin:CSR_Access
                CsrOut <= CsrFile[CsrIndex];
                TrapIsSet <= 1'b0;
                case(CsrOperation)   
                    csrrw: 
                    begin:CSR_RW
                        UpdateCsr((CsrIn), CsrIndex);
                    end:CSR_RW
                    
                    csrrs: 
                    begin:CSR_Set
                        if(RsNotZero)
                            UpdateCsr((CsrIn | CsrFile[CsrIndex]), CsrIndex);
                    end:CSR_Set
                    
                    csrrc: 
                    begin:CSR_Clear
                        if(RsNotZero)
                            UpdateCsr(~CsrIn & CsrFile[CsrIndex], CsrIndex);
                    end:CSR_Clear
                    
                    csrrwi: 
                    begin:Write_Immediate
                        UpdateCsr({27'b0, Rs}, CsrIndex);
                    end:Write_Immediate
                    
                    csrrsi: 
                    begin:Set_Immediate
                        if(RsNotZero)
                            UpdateCsr(CsrFile[CsrIndex] | {27'b0, Rs}, CsrIndex);
                    end:Set_Immediate
                    
                    csrrci: 
                    begin:Clear_Immediate
                        if(RsNotZero)
                            UpdateCsr(CsrFile[CsrIndex] & ~{27'b0, Rs}, CsrIndex);
                    end:Clear_Immediate
                endcase
            end:CSR_Access
            else
            begin:No_Operation
                CsrOut <= 'b0;
                TrapIsSet <= 1'b0;
            end:No_Operation
        end
    end:CSR_File

    assign RoundingMode = round_mode_t'(CsrFile[frm][2:0]);


endmodule
