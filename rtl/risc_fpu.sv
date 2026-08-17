// =============================================================================
// risc_fpu.sv
// -----------------------------------------------------------------------------
// Floating-Point Unit (FPU) for RISC-V processor.
//
// Responsibilities:
//   - Execute all RV32F floating-point instructions
//   - Dispatch operations to appropriate functional units
//   - Handle move operations between GPR and FPR
//   - Manage multi-cycle operations with busy/done handshake
//   - Generate status flags (overflow, underflow, NaN, infinity, zero)
//
// Instantiates:
//   - flp_add_sub: Addition and subtraction unit
//   - flp_mul: Multiplication unit
//   - flp_div: Division unit
//   - flp_sqrt: Square root unit
//
// Parameters:
//   - PRECISION: SINGLE (32-bit) or DOUBLE (64-bit)
//   - STAGES: Number of pipeline stages for FPU operations
// =============================================================================
import shared_pkg::*;

module risc_fpu #(
    parameter flp_t PRECISION   = SINGLE,
    parameter int   STAGES      = 4
)(
    // ─── Clock and reset ───────────────────────────────────────────────────────
    input  logic clk,                       // Clock signal
    input  logic rst,                       // Asynchronous reset

    // ─── Operand inputs ───────────────────────────────────────────────────────
    input  logic valid,                      // Operation valid signal
    input  logic [ (PRECISION == SINGLE) ? 31 : 63 :0] InA,  // Operand A
    input  logic [ (PRECISION == SINGLE) ? 31 : 63 :0] InB,  // Operand B
    input  fpr_t RdF,                       // Destination FPR index

    // ─── Control inputs ───────────────────────────────────────────────────────
    input  logic RegWrite,                  // FPR write enable
    input  round_mode_t round_mode,         // Rounding mode
    input  fpu_operation_t operation,       // FPU operation type
    input  move_operation_t MoveOperation,  // GPR/FPR move type

    // ─── Status outputs ─────────────────────────────────────────────────────
    output logic busy,                       // FPU busy flag
    output logic done,                       // FPU done flag
    output logic Overflow, Underflow, NaN, Inf, Zero, InvalidDiv,  // Status flags

    // ─── Result outputs ─────────────────────────────────────────────────────
    output logic [ (PRECISION == SINGLE) ? 31 : 63 :0] Result,  // Result
    output fpr_t RdFOut,                    // Destination FPR index (output)
    output logic RegWriteOut,               // FPR write enable (output)
    output move_operation_t MoveOperationOut // Move operation (output)
);

    // ─── Internal wires – operand routing ──────────────────────────────────
    logic [ (PRECISION == SINGLE) ? 31 : 63 :0] InAddSubA, InAddSubB;
    logic [ (PRECISION == SINGLE) ? 31 : 63 :0] InMulA, InMulB;
    logic [ (PRECISION == SINGLE) ? 31 : 63 :0] InDivA, InDivB;
    logic [ (PRECISION == SINGLE) ? 31 : 63 :0] InSqrt;
    logic [ (PRECISION == SINGLE) ? 31 : 63 :0] ResultAddSub, ResultMul, ResultDiv, ResultSqrt;
    logic [ (PRECISION == SINGLE) ? 31 : 63 :0] InA_reg, InB_reg;
    logic [ (PRECISION == SINGLE) ? 31 : 63 :0] Result_reg, Result_selected;

    localparam int WIDTH        = (PRECISION == SINGLE) ? 32 : 64;
    localparam int EXP_BITS     = (PRECISION == SINGLE) ? 8  : 11;
    localparam int FRAC_BITS    = (PRECISION == SINGLE) ? 23 : 52;
    localparam int BIAS         = (PRECISION == SINGLE) ? 127: 1023;
    localparam int WIDTHBITS    = $clog2(WIDTH);
    
    mode_t add_sub_mode;
    logic UseAddSub;
    logic UseMul;
    logic UseDiv;
    logic UseSqrt;

    logic OverflowAddSub, UnderflowAddSub, NaNAddSub, InfAddSub, ZeroAddSub;
    logic OverflowMul, UnderflowMul, NaNMul, InfMul, ZeroMul;
    logic OverflowDiv, UnderflowDiv, NaNDiv, InfDiv, ZeroDiv;
    logic InfSqrt, NanSqrt, ZeroSqrt;
    logic [ (PRECISION == SINGLE) ? 31 : 63 :0] SqrtMulA, SqrtMulB, SqrtAddSubA, SqrtAddSubB;
    logic SqrtMulValid;
    logic SqrtAddSubValid;
    mode_t SqrtAddSubMode;
    logic Subnormal,InvalidDivActual;
    logic AddSubBusy, AddSubDone;
    logic MulBusy, MulDone;
    logic DivBusy, DivDone;
    logic SqrtBusy, SqrtDone;
    logic req_valid_reg, req_accept, req_complete, req_uses_module, modules_busy;
    logic done_reg;
    logic Overflow_reg, Underflow_reg, NaN_reg, Inf_reg, Zero_reg, InvalidDiv_reg;
    logic Overflow_selected, Underflow_selected, NaN_selected, Inf_selected, Zero_selected, InvalidDiv_selected;
    logic ClassNaN, ClassInf, ClassZero;
    round_mode_t round_mode_reg;
    fpu_operation_t operation_reg;
    fpr_t RdF_reg;
    logic RegWrite_reg;
    move_operation_t MoveOperation_reg;

    function logic signed [WIDTH-1:0] FloatToInt 
    (
        input  logic [WIDTH-1:0] float_in,
        input  logic SignOrUnsign,
        input round_mode_t round
    );
        logic sign;
        logic [EXP_BITS-1:0]  exponent;
        logic [FRAC_BITS:0] mantissa; // Including hidden bit
        logic signed [EXP_BITS:0] exp_unbiased;
        logic [EXP_BITS-1:0] shift_amount;
        bit frac_not_zero;
        logic guard_bit;
        logic sticky_bit;
        logic round_increment;
        logic special; // Flag for special cases where rounding not needed
        logic is_zero;
        logic overflow_from_round;
        logic inf;
        logic nan;
        int sticky_idx;

        sign     = float_in[WIDTH-1];
        exponent = float_in[WIDTH-2 -:EXP_BITS];
        mantissa = {1'b1, float_in[FRAC_BITS-1:0]}; // Append hidden bit
        frac_not_zero = |float_in[FRAC_BITS-1:0];
        FloatToInt = 0;
        guard_bit = 1'b0;
        sticky_bit = 1'b0;
        round_increment = 1'b0;
        overflow_from_round = 1'b0;
        inf = ((exponent == {EXP_BITS{1'b1}}) && !frac_not_zero);
        nan = ((exponent == {EXP_BITS{1'b1}}) &&  frac_not_zero);
        is_zero = ((exponent == '0) && !frac_not_zero);
        special = 0;

        exp_unbiased = exponent - BIAS;
        if(sign && !SignOrUnsign)
        begin
            FloatToInt = 32'h00000000; // Max Negative for unsigned input
        end
        else
        begin
            if(nan)
            begin
                FloatToInt = 32'h7FFFFFFF; // Max Positive for NaN
                special = 1'b1;
            end
            else if(inf)
            begin
                if(sign && SignOrUnsign)
                begin
                    FloatToInt = 32'h80000000; // Max Negative
                end
                else
                begin
                    FloatToInt = 32'h7FFFFFFF; // Max Positive
                end
                special = 1'b1;
            end
            else if (is_zero) 
            begin
                // Exact zero is always converted without rounding.
                FloatToInt = 32'd0;
                special = 1'b1;
            end
            else if ((|exp_unbiased[EXP_BITS-1:WIDTHBITS])|| (& exp_unbiased[WIDTHBITS-1:0])) //If exponent is greater than or equal to WIDTH, it will overflow
            begin
                special = 1'b1;
                if(sign && SignOrUnsign)
                begin
                    FloatToInt = 32'h80000000; // Max Negative
                end
                else
                begin
                    FloatToInt = 32'h7FFFFFFF; // Max Positive
                end
            end
            else if ((!exponent) || exp_unbiased[EXP_BITS])
            begin
                // Subnormal values and magnitudes below 1.0 still need rounding-mode handling.
                FloatToInt = '0;

                if ((exponent != '0) && (exp_unbiased == -1))
                begin
                    guard_bit = 1'b1;
                    sticky_bit = frac_not_zero;
                end
                else
                begin
                    guard_bit = 1'b0;
                    sticky_bit = frac_not_zero;
                end
            end
            else if (exp_unbiased > FRAC_BITS) 
            begin
                // Overflow beyond mantissa width
                FloatToInt = mantissa << (exp_unbiased[EXP_BITS-1:0] - FRAC_BITS);
            end 
            else 
            begin
                // Shift mantissa according to exponent
                shift_amount = FRAC_BITS - exp_unbiased[EXP_BITS-1:0];
                FloatToInt = mantissa >> shift_amount;
                if (shift_amount != 0)
                begin
                    guard_bit = mantissa[shift_amount-1];
                    if (shift_amount > 1)
                    begin
                        for (sticky_idx = 0; sticky_idx < (shift_amount - 1); sticky_idx++)
                            sticky_bit |= mantissa[sticky_idx];
                    end
                end
            end

            if(!special)
            begin
                case(round)
                    RNE:
                    begin
                        round_increment = guard_bit && (sticky_bit || FloatToInt[0]);
                    end
                    RTZ:
                    begin
                        round_increment = 1'b0;
                    end
                    RDN:
                    begin
                        round_increment = sign && (guard_bit || sticky_bit);
                    end
                    RUP:
                    begin
                        round_increment = !sign && (guard_bit || sticky_bit);
                    end
                    RMM:
                    begin
                        round_increment = guard_bit;
                    end
                    default:
                    begin
                        round_increment = guard_bit && (sticky_bit || FloatToInt[0]);
                    end
                endcase

                overflow_from_round = round_increment &&
                                    !sign &&
                                    SignOrUnsign &&
                                    (FloatToInt == {1'b0, {(WIDTH-1){1'b1}}});

                if (overflow_from_round)
                begin
                    FloatToInt = 32'h7FFFFFFF;
                    special = 1'b1;
                end
                else
                begin
                    FloatToInt = FloatToInt + round_increment;
                    if(SignOrUnsign && sign)
                        FloatToInt = -FloatToInt;
                end
            end
        end
    
    endfunction: FloatToInt

    function logic [WIDTH-1:0] IntToFloat
    (
        input  logic signed [WIDTH-1:0] int_in,
        input  logic SignOrUnsign,
        input  round_mode_t round
    );
        logic sign;
        logic [WIDTH-1:0] abs_val;
        logic [EXP_BITS-1:0] exponent;
        logic [FRAC_BITS:0] mantissa;
        logic signed [EXP_BITS:0] exp_unbiased;
        int msb_index;
        logic signed [1:0] round_up_down;
        logic [WIDTH-1:0] result;

        // Step 1: Extract sign and absolute value
        sign    = int_in[WIDTH-1];
        abs_val = sign ? -int_in : int_in;
        mantissa = 0;
        msb_index = 0;
        exponent = 0;

        // Step 2: Handle zero
        if (abs_val == 0) 
        begin
            IntToFloat = {sign, {EXP_BITS{1'b0}}, {FRAC_BITS{1'b0}}};
        end
        else 
        begin
            // Step 3: Find MSB position (normalization)
            for (int i = 0; (i < WIDTH); i++) 
            begin
                if(abs_val[i])
                begin
                    msb_index = i;
                end
            end
            // Step 4: Compute unbiased exponent
            
            //exp_unbiased = msb_index;

            // Step 5: Bias exponent
            exponent = msb_index + BIAS;

            // Step 6: Normalize mantissa (drop hidden bit)
            abs_val = (abs_val << (WIDTH - 1 - msb_index));
            mantissa = abs_val[WIDTH-2 -: FRAC_BITS];

            // Step 7: Rounding logic (similar to FloatToInt)
            round_up_down = 0;
            case(round)
                RNE: round_up_down = mantissa[0]; // round to nearest even
                RTZ: round_up_down = 0;
                RDN: round_up_down = (sign) ? 2'b11 : 2'b0;
                RUP: round_up_down = (!sign) ? 2'b01 : 2'b0;
                RMM: round_up_down = 2'b01;
            endcase

            mantissa = mantissa + round_up_down;
            if(mantissa == (1 << FRAC_BITS)) // Check for mantissa overflow (e.g., 1.111... + 0.000... = 10.000...)
            begin
                mantissa = 0;
                exponent ++;
            end
            // Step 8: Pack into IEEE-754 format
            IntToFloat = {sign & SignOrUnsign, exponent, mantissa[FRAC_BITS-1:0]};
            end
    endfunction: IntToFloat

    // Classification function that returns 4-bit encoded status
    // Bit 3: is_subnormal, Bit 2: is_nan, Bit 1: is_infinity, Bit 0: is_zero
    function automatic logic [3:0] classify_value(
        input logic [EXP_BITS-1:0] exp,
        input logic [FRAC_BITS-1:0] frac
    );
        logic [3:0] classification;
        classification[0] = (exp == 0) && (frac == 0);                              // is_zero
        classification[1] = (exp == {EXP_BITS{1'b1}}) && (frac == 0);              // is_infinity
        classification[2] = (exp == {EXP_BITS{1'b1}}) && (frac != 0);              // is_nan
        classification[3] = (exp == 0) && (frac != 0);                             // is_subnormal
        return classification;
    endfunction: classify_value

    always_ff @(posedge clk or negedge rst)
    begin
        if (!rst)
        begin
            InA_reg <= '0;
            InB_reg <= '0;
            round_mode_reg <= RNE;
            operation_reg <= FADD_S;
            req_valid_reg <= 1'b0;
            done_reg <= 1'b0;
            Overflow_reg <= 1'b0;
            Underflow_reg <= 1'b0;
            NaN_reg <= 1'b0;
            Inf_reg <= 1'b0;
            Zero_reg <= 1'b0;
            InvalidDiv_reg <= 1'b0;
            Result_reg <= '0;
            RdF_reg <= f0;
            RegWrite_reg <= 1'b0;
            MoveOperation_reg <= FPUToFPU;        
        end
        else
        begin
            if (req_accept)
            begin
                InA_reg <= InA;
                InB_reg <= InB;
                round_mode_reg <= round_mode;
                operation_reg <= operation;
                req_valid_reg <= 1'b1;
                done_reg <= 1'b0;
                RdF_reg <= RdF;
                RegWrite_reg <= RegWrite;
                MoveOperation_reg <= MoveOperation;
            end
            else if (req_complete)
            begin
                Result_reg <= Result_selected;
                Overflow_reg <= Overflow_selected;
                Underflow_reg <= Underflow_selected;
                NaN_reg <= NaN_selected;
                Inf_reg <= Inf_selected;
                Zero_reg <= Zero_selected;
                InvalidDiv_reg <= InvalidDiv_selected;
                done_reg <= 1'b1;
                req_valid_reg <= 1'b0;
            end
            else if(done_reg)
            begin
                done_reg <= 1'b0;
                RdF_reg <= f0;
                RegWrite_reg <= 1'b0;
                MoveOperation_reg <= FPUToFPU;
                InA_reg <= '0;
                InB_reg <= '0;
                round_mode_reg <= RNE;
                operation_reg <= FADD_S;
                Result_reg <= '0;
                Overflow_reg <= 1'b0;
                Underflow_reg <= 1'b0;
                NaN_reg <= 1'b0;
                Inf_reg <= 1'b0;
                Zero_reg <= 1'b0;
                InvalidDiv_reg <= 1'b0;
            end
        end
    end

    always_comb
    begin
        InAddSubA   = '0;
        InAddSubB   = '0;
        InMulA      = '0;
        InMulB      = '0;
        InDivA      = '0;
        InDivB      = '0;
        InSqrt      = '0;
        ClassNaN    = 1'b0;
        ClassInf    = 1'b0;
        ClassZero   = 1'b0;
        Subnormal   = 1'b0;
        add_sub_mode = ADD_FLP;
        UseAddSub = 1'b0;
        UseMul = 1'b0;
        UseDiv = 1'b0;
        UseSqrt = 1'b0;
        Result_selected = '0;
        Overflow_selected = 1'b0;
        Underflow_selected = 1'b0;
        NaN_selected = 1'b0;
        Inf_selected = 1'b0;
        Zero_selected = 1'b0;
        InvalidDiv_selected = 1'b0;
        req_uses_module = 1'b0;

        case(operation_reg)
                FADD_S:
                begin
                    InAddSubA       = InA_reg;
                    InAddSubB       = InB_reg;
                    add_sub_mode    = ADD_FLP;
                    UseAddSub       = 1'b1;
                    req_uses_module = 1'b1;
                    Overflow_selected = OverflowAddSub;
                    Underflow_selected = UnderflowAddSub;
                    NaN_selected = NaNAddSub;
                    Inf_selected = InfAddSub;
                    Zero_selected = ZeroAddSub;
                    Result_selected = ResultAddSub;
                end
                FSUB_S:
                begin
                    InAddSubA       = InA_reg;
                    InAddSubB       = InB_reg;
                    add_sub_mode    = SUB_FLP;
                    UseAddSub       = 1'b1;
                    req_uses_module = 1'b1;
                    Overflow_selected = OverflowAddSub;
                    Underflow_selected = UnderflowAddSub;
                    NaN_selected = NaNAddSub;
                    Inf_selected = InfAddSub;
                    Zero_selected = ZeroAddSub;
                    Result_selected = ResultAddSub;
                end
                FMUL_S:
                begin
                    InMulA          = InA_reg;
                    InMulB          = InB_reg;
                    UseMul          = 1'b1;
                    req_uses_module = 1'b1;
                    Overflow_selected = OverflowMul;
                    Underflow_selected = UnderflowMul;
                    NaN_selected = NaNMul;
                    Inf_selected = InfMul;
                    Zero_selected = ZeroMul;
                    Result_selected = ResultMul;
                end
                FDIV_S:
                begin
                    InDivA          = InA_reg;
                    InDivB          = InB_reg;
                    UseDiv          = 1'b1;
                    req_uses_module = 1'b1;
                    Overflow_selected = OverflowDiv;
                    Underflow_selected = UnderflowDiv;
                    NaN_selected = NaNDiv;
                    Inf_selected = InfDiv;
                    Zero_selected = ZeroDiv;
                    InvalidDiv_selected = InvalidDivActual;
                    Result_selected = ResultDiv;
                end
                FSQRT_S:
                begin
                    InSqrt          = InA_reg;
                    InMulA          = SqrtMulA;
                    InMulB          = SqrtMulB;
                    InAddSubA       = SqrtAddSubA;
                    InAddSubB       = SqrtAddSubB;
                    add_sub_mode    = SqrtAddSubMode;
                    UseMul          = SqrtMulValid;
                    UseAddSub       = SqrtAddSubValid;
                    UseSqrt         = 1'b1;
                    req_uses_module = 1'b1;
                    NaN_selected = NanSqrt;
                    Inf_selected = InfSqrt;
                    Zero_selected = ZeroSqrt;
                    Result_selected = ResultSqrt;
                end
                FSGNJ_S:
                begin
                    Result_selected = {InB_reg[(PRECISION == SINGLE) ? 31 : 63], InA_reg[(PRECISION == SINGLE) ? 30 : 62 :0]};
                end
                FSGNJN_S:
                begin
                    Result_selected = {~InB_reg[(PRECISION == SINGLE) ? 31 : 63], InA_reg[(PRECISION == SINGLE) ? 30 : 62 :0]};
                end
                FSGNJX_S:
                begin
                    Result_selected = {InA_reg[(PRECISION == SINGLE) ? 31 : 63] ^ InB_reg[(PRECISION == SINGLE) ? 31 : 63], InA_reg[(PRECISION == SINGLE) ? 30 : 62 :0]};
                end
                FMIN_S:
                begin
                    add_sub_mode    = SUB_FLP;
                    UseAddSub       = 1'b1;
                    req_uses_module = 1'b1;
                    InAddSubA = InA_reg;
                    InAddSubB = InB_reg;
                    if((ResultAddSub[(PRECISION == SINGLE) ? 31 : 63] == 1'b1) && (NaNAddSub == 1'b0))
                        Result_selected = InA_reg;
                    else
                        Result_selected = InB_reg;
                end
                FMAX_S:
                begin
                    add_sub_mode    = SUB_FLP;
                    UseAddSub       = 1'b1;
                    req_uses_module = 1'b1;
                    InAddSubA = InA_reg;
                    InAddSubB = InB_reg;
                    if(ResultAddSub[(PRECISION == SINGLE) ? 31 : 63] == 1'b0 && NaNAddSub == 1'b0)
                        Result_selected = InA_reg;
                    else
                        Result_selected = InB_reg;
                end
                FCVT_W_S:
                begin
                    Result_selected = FloatToInt(InA_reg, 1'b1, round_mode_reg);
                end
                FCVT_WU_S:
                begin
                    Result_selected = FloatToInt(InA_reg, 1'b0, round_mode_reg);
                end
                FMV_X_S:
                begin
                    Result_selected = InA_reg;
                end
                FEQ_S:
                begin
                    add_sub_mode    = SUB_FLP;
                    UseAddSub       = 1'b1;
                    req_uses_module = 1'b1;
                    InAddSubA = InA_reg;
                    InAddSubB = InB_reg;
                    if(ZeroAddSub == 1'b1 && NaNAddSub == 1'b0)
                        Result_selected = 32'd1;
                    else
                        Result_selected = 32'd0;
                end
                FLT_S:
                begin
                    add_sub_mode    = SUB_FLP;
                    UseAddSub       = 1'b1;
                    req_uses_module = 1'b1;
                    InAddSubA = InA_reg;
                    InAddSubB = InB_reg;
                    if((!NaNAddSub) && (ResultAddSub[(PRECISION == SINGLE) ? 31 : 63] == 1'b1) && (!ZeroAddSub))
                        Result_selected = 32'd1;
                    else
                        Result_selected = 32'd0;
                end
                FLE_S:
                begin
                    add_sub_mode    = SUB_FLP;
                    UseAddSub       = 1'b1;
                    req_uses_module = 1'b1;
                    InAddSubA = InA_reg;
                    InAddSubB = InB_reg;
                    if(((ResultAddSub[(PRECISION == SINGLE) ? 31 : 63]) && (!NaNAddSub)) || (ZeroAddSub))
                        Result_selected = 32'd1;
                    else
                        Result_selected = 32'd0;
                end
                FCLASS_S:
                begin
                    if(!InA_reg[WIDTH-2 -: EXP_BITS])
                    begin
                        if(!InA_reg[FRAC_BITS-1:0])
                            ClassZero = 1'b1;
                        else
                            Subnormal = 1'b1;
                    end
                    if(InA_reg[WIDTH-2 -: EXP_BITS] == {EXP_BITS{1'b1}})
                    begin
                        if(!InA_reg[FRAC_BITS-1:0])
                            ClassInf = 1'b1;
                        else
                            ClassNaN = 1'b1;
                    end
                    Zero_selected = ClassZero;
                    Inf_selected = ClassInf;
                    NaN_selected = ClassNaN;
                    Result_selected[0] = ClassInf & (InA_reg[(PRECISION == SINGLE) ? 31 : 63]);
                    Result_selected[1] = (InA_reg[(PRECISION == SINGLE) ? 31 : 63]) && !(ClassNaN | ClassInf | ClassZero | Subnormal);
                    Result_selected[2] = Subnormal & (InA_reg[(PRECISION == SINGLE) ? 31 : 63]);
                    Result_selected[3] = ClassZero & (InA_reg[(PRECISION == SINGLE) ? 31 : 63]);
                    Result_selected[4] = ClassZero & !(InA_reg[(PRECISION == SINGLE) ? 31 : 63]);
                    Result_selected[5] = Subnormal & !(InA_reg[(PRECISION == SINGLE) ? 31 : 63]);
                    Result_selected[6] = !(ClassNaN | ClassInf | ClassZero | Subnormal) & !(InA_reg[(PRECISION == SINGLE) ? 31 : 63]);
                    Result_selected[7] = ClassInf & !(InA_reg[(PRECISION == SINGLE) ? 31 : 63]);
                    Result_selected[8] = ClassNaN & (InA_reg[(PRECISION == SINGLE) ? 22 : 51]); 
                    Result_selected[9] = ClassNaN & !(InA_reg[(PRECISION == SINGLE) ? 22 : 51]);
                end
                FCVT_S_W:
                begin
                    Result_selected = IntToFloat(InA_reg, 1'b1, round_mode_reg);
                end
                FCVT_S_WU:
                begin
                    Result_selected = IntToFloat(InA_reg, 1'b0, round_mode_reg);          
                end
                FMV_S_X:
                begin
                    Result_selected = InA_reg;
                end
            endcase
    end

    always_comb
    begin
        modules_busy = AddSubBusy | MulBusy | DivBusy | SqrtBusy;
        req_accept = valid && !req_valid_reg && !modules_busy;
        req_complete = 1'b0;

        if (req_valid_reg)
        begin
            if (req_uses_module)
            begin
                if (UseSqrt)
                    req_complete = SqrtDone;
                else
                req_complete = ((UseAddSub && AddSubDone) || (UseMul && MulDone) || (UseDiv && DivDone) || (UseSqrt && SqrtDone));
            end
            else
                req_complete = 1'b1;
        end
    end

    assign busy = req_valid_reg;
    assign done = done_reg;
    assign Overflow = Overflow_reg;
    assign Underflow = Underflow_reg;
    assign NaN = NaN_reg;
    assign Inf = Inf_reg;
    assign Zero = Zero_reg;
    assign InvalidDiv = InvalidDiv_reg;
    assign Result = Result_reg;
    
    always_comb
    begin
        MoveOperationOut = FPUToFPU;
        RdFOut = f0;
        RegWriteOut = 1'b0;
        if(done_reg)
        begin
            MoveOperationOut = MoveOperation_reg;
            RdFOut = RdF_reg;
            RegWriteOut = RegWrite_reg;
        end
    end

    flp_add_sub #(.PRECISION(PRECISION)) add_sub_unit 
    (
        .a(InAddSubA),
        .b(InAddSubB),
        .clk(clk),
        .rst(rst),
        .valid(req_valid_reg & UseAddSub),
        .round_mode( round_mode_reg),
        .mode(add_sub_mode),
        .busy(AddSubBusy),
        .done(AddSubDone),
        .Overflow(OverflowAddSub),
        .Underflow(UnderflowAddSub),
        .NaN(NaNAddSub),
        .Inf(InfAddSub),
        .Zero(ZeroAddSub),
        .result(ResultAddSub)
    );

    flp_mul #(.PRECISION(PRECISION),.STAGES(STAGES)) mul_unit 
    (
        .a(InMulA),
        .b(InMulB),
        .clk(clk),
        .rst(rst),
        .valid(req_valid_reg & UseMul),
        .round_mode(round_mode_reg),
        .busy(MulBusy),
        .done(MulDone),
        .Overflow(OverflowMul),
        .Underflow(UnderflowMul),
        .NaN(NaNMul),
        .Inf(InfMul),
        .Zero(ZeroMul),
        .result(ResultMul)
    );

    flp_div #(.PRECISION(PRECISION),.STAGES(STAGES)) div_unit 
    (
        .a(InDivA),
        .b(InDivB),
        .clk(clk),
        .rst(rst),
        .valid(req_valid_reg & UseDiv),
        .round_mode(round_mode_reg),
        .busy(DivBusy),
        .done(DivDone),
        .Overflow(OverflowDiv),
        .Underflow(UnderflowDiv),
        .NaN(NaNDiv),
        .Inf(InfDiv),
        .Zero(ZeroDiv),
        .invalid(InvalidDivActual),
        .result(ResultDiv)
    );

    flp_sqrt #(.PRECISION(PRECISION),.STAGES(STAGES)) sqrt_unit 
    (
        .clk(clk),
        .rst(rst),
        .valid(req_valid_reg & UseSqrt),
        .a(InSqrt),
        .mul_a(SqrtMulA),
        .mul_b(SqrtMulB),
        .mul_valid(SqrtMulValid),
        .mul_result(ResultMul),
        .mul_done(MulDone),
        .add_sub_a(SqrtAddSubA),
        .add_sub_b(SqrtAddSubB),
        .add_sub_valid(SqrtAddSubValid),
        .add_sub_mode(SqrtAddSubMode),
        .add_sub_result(ResultAddSub),
        .add_sub_done(AddSubDone),
        .busy(SqrtBusy),
        .done(SqrtDone),
        .Inf(InfSqrt),
        .NaN(NanSqrt),
        .Zero(ZeroSqrt),
        .result(ResultSqrt)
    );
endmodule: risc_fpu
