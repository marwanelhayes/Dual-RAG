// =============================================================================
// shared_pkg.sv
// -----------------------------------------------------------------------------
// Shared package containing all common data types, enums, parameters, and
// constants used across both design and verification.
//
// Responsibilities:
//   - Define RISC-V opcode, ALU operation, and branch type enumerations
//   - Define GPR and FPR register indices
//   - Define CSR register indices and trap cause codes
//   - Define floating-point operation types and rounding modes
//   - Define pipeline state machine types for FPU operations
//   - Provide global design parameters (data width, address width, precision)
// =============================================================================
package shared_pkg;
    typedef enum logic [6:0] 
    { 
        R_TYPE       = 7'b011_0011 , 
        LOAD         = 7'b000_0011 , 
        S_TYPE       = 7'b010_0011 , 
        B_TYPE       = 7'b110_0011 , 
        I_TYPE       = 7'b001_0011 , 
        JAL          = 7'b110_1111 , 
        JALR         = 7'b110_0111 , 
        CSR          = 7'b111_0011 ,
        FLOATING_PT  = 7'b101_0011
    } opcode_t;

    typedef enum logic [4:0] 
    { 
        ADD     = 0     , 
        SUB     = 1     , 
        AND     = 2     , 
        OR      = 3     , 
        XOR     = 4     , 
        SLT     = 5     , 
        SLTU    = 6     , 
        SLL     = 7     , 
        SRL     = 8     , 
        SRA     = 9     ,
        MUL     = 10    ,
        MULH    = 11    ,
        MULHSU  = 12    ,
        MULHU   = 13    ,
        DIV     = 14    ,
        DIVU    = 15    ,
        REM     = 16    ,
        REMU    = 17    
    } alu_operation_t;

    typedef enum logic [1:0] 
    { 
        unsign      = 2'b00 , 
        sign        = 2'b01 , 
        signXunsign = 2'b10 ,
        error       = 2'b11
    } mul_sel_t;

    typedef enum logic [4:0] 
    { 
        zero, 
        ra, 
        sp, 
        gp, 
        tp, 
        t0, 
        t1, 
        t2, 
        s0_fp, 
        s1, 
        a0, 
        a1, 
        a2, 
        a3, 
        a4, 
        a5, 
        a6, 
        a7, 
        s2, 
        s3, 
        s4, 
        s5, 
        s6, 
        s7, 
        s8, 
        s9, 
        s10, 
        s11, 
        t3, 
        t4, 
        t5, 
        t6
    } gpr_t;

    typedef enum logic [2:0] 
    { 
        BEQ     = 3'b000 , 
        BNE     = 3'b001 , 
        BLT     = 3'b100 , 
        BGE     = 3'b101 , 
        BLTU    = 3'b110 , 
        BGEU    = 3'b111
    } branch_t;

    typedef enum logic [2:0] 
    { 
        csrrw   = 3'b001 , 
        csrrs   = 3'b010 , 
        csrrc   = 3'b011 , 
        csrrwi  = 3'b101 , 
        csrrsi  = 3'b110 , 
        csrrci  = 3'b111 , 
        system  = 3'b000
    } csr_t;

    typedef enum logic [11:0] 
    { 
        fflags      = 12'h001 ,
        frm         = 12'h002 ,
        fcsr        = 12'h003 ,
        mstatus     = 12'h300 ,
        misa        = 12'h301 ,
        medeleg     = 12'h302 ,
        mideleg     = 12'h303 ,
        mie         = 12'h304 ,
        mtvec       = 12'h305 ,
        mscratch    = 12'h340 ,
        mepc        = 12'h341 ,
        mcause      = 12'h342 ,
        mbadaddr    = 12'h343 ,
        mip         = 12'h344 , 
        mcycle      = 12'hC00 ,  
        mhartid     = 12'hF14 ,
        mvendorid   = 12'hF11
    } csr_index_t;

    typedef enum logic [2:0] 
    { 
        B   = 3'b000 , 
        HW  = 3'b001 , 
        W   = 3'b010 , 
        BU  = 3'b100 , 
        HWU = 3'b101
    } load_store_t;

    typedef enum logic [1:0] 
    { 
        ALUToReg    = 2'b00 , 
        MemToReg    = 2'b01 ,   
        PCToReg     = 2'b10 ,
        CSRToReg    = 2'b11
    } selector_t;

    typedef enum logic [11:0] 
    {
        NoTraps                                             = 12'b0000_0000_0000 ,
        InstructionAddressMisalignedOrUserSoftwareInterrupt = 12'b0000_0000_0001 , 
        InstructionAccessFaultOrSupervisorSoftwareInterrupt = 12'b0000_0000_0010 , 
        IllegalInstructionOrHypervisorSoftwareInterrupt     = 12'b0000_0000_0100 , 
        BreakpointOrMachineSoftwareInterrupt                = 12'b0000_0000_1000 , 
        LoadAddressMisalignedOrUserSoftwareInterrupt        = 12'b0000_0001_0000 , 
        LoadAddressFaultOrSupervisorTimerInterrupt          = 12'b0000_0010_0000 , 
        StoreAddressMisalignedOrHyperVisorTimerInterrupt    = 12'b0000_0100_0000 , 
        StoreAddressFaultOrMachineTimerInterrupt            = 12'b0000_1000_0000 , 
        EcallUOrUserExternalInterrupt                       = 12'b0001_0000_0000 , 
        EcallSOrSupervisorExternalInterrupt                 = 12'b0010_0000_0000 , 
        EcallHOrHypervisorExternalInterrupt                 = 12'b0100_0000_0000 , 
        EcallMOrMachineExternalInterrupt                    = 12'b1000_0000_0000
    } traps_t;

    typedef enum bit 
    {
        SINGLE = 1'b0,
        DOUBLE = 1'b1 // Implemented except in the square root operation
    } flp_t;

    typedef enum bit 
    {
        ADD_FLP   = 1'b0,
        SUB_FLP   = 1'b1
    } mode_t;

    typedef enum logic [2:0] 
    {
        RNE     = 3'b000, // toward nearest, ties to even
        RTZ     = 3'b001, // toward zero
        RDN     = 3'b010, // toward +∞
        RUP     = 3'b011, // toward -∞
        RMM     = 3'b100, // toward nearest, ties to max magnitude
        DYN     = 3'b111  // dynamic rounding mode
    } round_mode_t;

    typedef enum logic [1:0]
    {
        ADD_FPU      = 2'b00,
        SUB_FPU      = 2'b01,
        MUL_FPU      = 2'b10,
        DIV_FPU      = 2'b11
    } fpu_mode_t;

    typedef enum logic [4:0]
    {
        f0,
        f1,
        f2,
        f3,
        f4,
        f5,
        f6,
        f7,
        f8,
        f9,
        f10,
        f11,
        f12,
        f13,
        f14,
        f15,
        f16,
        f17,
        f18,
        f19,
        f20,
        f21,
        f22,
        f23,
        f24,
        f25,
        f26,
        f27,
        f28,
        f29,
        f30,
        f31
    } fpr_t;

    typedef enum logic [4:0] 
    { 
        FADD_S      = 0     , 
        FSUB_S      = 1     , 
        FMUL_S      = 2     , 
        FDIV_S      = 3     , 
        FSQRT_S     = 4     ,
        FSGNJ_S     = 5     ,
        FSGNJN_S    = 6     ,
        FSGNJX_S    = 7     ,
        FMIN_S      = 8     ,
        FMAX_S      = 9     ,
        FCVT_W_S    = 10    ,
        FCVT_WU_S   = 11    ,
        FMV_X_S     = 12    ,
        FEQ_S       = 13    ,
        FLT_S       = 14    ,
        FLE_S       = 15    ,
        FCLASS_S    = 16    ,
        FCVT_S_W    = 17    ,
        FCVT_S_WU   = 18    ,
        FMV_S_X     = 19    ,
        NOOPERATION = 20
    } fpu_operation_t;

    typedef enum logic [1:0]
    {
        FPUToReg = 2'b00,
        RegToFPU = 2'b01,
        FPUToFPU = 2'b10
    } move_operation_t;

    typedef enum logic [2:0]
    {
        INIT    = 3'b000,
        IDLE    = 3'b001,
        SPLIT   = 3'b010,
        ALIGN   = 3'b011,
        TRUEADD = 3'b111,
        TRUESUB = 3'b100,
        DONE    = 3'b101
    } flp_add_sub_state_t;

    typedef enum logic [2:0]
    {
        INIT_MUL  = 3'b000,
        IDLE_MUL  = 3'b001,
        SPLIT_MUL = 3'b010,
        MUL_MUL   = 3'b011,
        ROUND_MUL = 3'b100,
        DONE_MUL  = 3'b101
    } flp_mul_state_t;

    typedef enum logic [2:0]
    {
        INIT_DIV  = 3'b000,
        IDLE_DIV  = 3'b001,
        SPLIT_DIV = 3'b010,
        DIV_DIV   = 3'b011,
        ROUND_DIV = 3'b100,
        DONE_DIV  = 3'b101
    } flp_div_state_t;

    typedef enum logic [2:0]
    {
        SQRT_INIT    = 3'b000,
        SQRT_IDLE    = 3'b001,
        SQRT_MUL1    = 3'b010,
        SQRT_MUL2    = 3'b011,
        SQRT_ADD_SUB = 3'b100,
        SQRT_MUL3    = 3'b101,
        SQRT_MUL4    = 3'b110,
        SQRT_DONE    = 3'b111
    } flp_sqrt_state_t;

    typedef enum logic [2:0] {
        AXI_IDLE    = 3'b000,
        AXI_RD_ADDR = 3'b001,
        AXI_RD_DATA = 3'b010,
        AXI_WR_ADDR = 3'b011,
        AXI_WR_DATA = 3'b100,
        AXI_WR_RESP = 3'b101
    } axi_state_t;

    typedef enum logic [1:0] {
        FIXED    = 2'b00,
        INCR     = 2'b01,
        WRAP     = 2'b10,
        RESERVED = 2'b11
    } axi_burst_t;

    typedef enum logic [2:0] {
        AXI_OKAY        = 3'b000,
        AXI_EXOKAY      = 3'b001,
        AXI_SLVERR      = 3'b010,
        AXI_DECERR      = 3'b011,
        AXI_PREFETCH    = 3'b100,
        AXI_TRANSFAULT  = 3'b101,
        AXI_OKAYDIRTY   = 3'b110,
        AXI_RESERVED    = 3'b111
    } axi_resp_t;

    // Parameter declaration section for clean code and easy modification
    // You can modify these parameters to change the design specifications
    // These parameters are used across the design and verification packages, so changing them here will reflect in all the relevant files     
    parameter int   ALU_OP                  = 4;
    parameter int   FINAL_DATA_WIDTH        = 32;
    parameter int   FINAL_ADDR_WIDTH        = 10;
    parameter flp_t FINAL_PRECISION         = SINGLE;
    parameter int   FINAL_FLP_WIDTH         = (FINAL_PRECISION == SINGLE) ? 32 : 64;
    parameter int   FINAL_FLP_EXP_BITS      = (FINAL_PRECISION == SINGLE) ? 8  : 11;
    parameter int   FINAL_FLP_FRAC_BITS     = (FINAL_PRECISION == SINGLE) ? 23 : 52;
    parameter int   FINAL_FLP_BIAS          = (FINAL_PRECISION == SINGLE) ? 127: 1023;
    parameter int   CLK_PERIOD              = 10;
    parameter int   CACHE_TOTAL_LINES       = (2 ** 5);                          
    parameter int   CACHE_WAY               = 16;
    parameter int   CACHE_LINE_WORDS        = 4;                           
    parameter bit   ICACHE_READ_ONLY        = 1'b1;
    parameter bit   DCACHE_READ_ONLY        = 1'b0;                         
    parameter int   CACHE_AXI_SIZE          = 4;

    // ── Verification reference image path ─────────────────────────────────
    // Used by predictor and scoreboard so memory contents stay in sync with
    // the design's instruction-memory ROM image.
    parameter string INSTR_MEM_PATH         = "/home/marwan-ahmed/Work/RiscV/Python/binary1.txt";

endpackage:shared_pkg
