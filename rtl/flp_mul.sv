// =============================================================================
// flp_mul.sv
// -----------------------------------------------------------------------------
// Floating-point multiplication unit for RISC-V FPU.
//
// Responsibilities:
//   - Perform IEEE 754 single/double precision multiplication
//   - Handle special cases (NaN, infinity, zero, denormal)
//   - Support all rounding modes (RNE, RTZ, RDN, RUP, RMM, DYN)
//   - Multi-stage pipelined multiplication with configurable latency
//   - Provide busy/done handshake for flow control
//
// Parameters:
//   - PRECISION: SINGLE (32-bit) or DOUBLE (64-bit)
//   - WIDTH: Total bit width (32 or 64)
//   - EXP_BITS: Exponent bits (8 for single, 11 for double)
//   - FRAC_BITS: Fraction bits (23 for single, 52 for double)
//   - BIAS: Exponent bias (127 for single, 1023 for double)
//   - STAGES: Number of pipeline stages (default 4)
// =============================================================================
import shared_pkg::*;

module flp_mul
#(
    parameter flp_t PRECISION   = SINGLE,
    parameter int   WIDTH       = (PRECISION == SINGLE) ? 32 : 64,
    parameter int   EXP_BITS    = (PRECISION == SINGLE) ? 8  : 11,
    parameter int   FRAC_BITS   = (PRECISION == SINGLE) ? 23 : 52,
    parameter int   BIAS        = (PRECISION == SINGLE) ? 127: 1023,
    parameter int   STAGES      = 4
)(
    // ─── Operand inputs ───────────────────────────────────────────────────────
    input  logic [WIDTH-1:0] a,            // First floating-point operand
    input  logic [WIDTH-1:0] b,            // Second floating-point operand

    // ─── Clock and reset ───────────────────────────────────────────────────────
    input  logic clk,                       // Clock signal
    input  logic rst,                       // Asynchronous reset

    // ─── Control signals ───────────────────────────────────────────────────
    input  logic valid,                     // Operation valid (start)
    input  round_mode_t round_mode,          // Rounding mode selection

    // ─── Status outputs ───────────────────────────────────────────────────
    output logic busy,                       // Operation in progress
    output logic done,                       // Operation complete
    output logic Overflow,
    output logic Underflow,
    output logic NaN,
    output logic Inf,
    output logic Zero,
    output logic [WIDTH-1:0] result
);

    logic [WIDTH-1:0] a_reg, a_next;
    logic [WIDTH-1:0] b_reg, b_next;
    round_mode_t round_mode_reg, round_mode_next;
    flp_mul_state_t state_reg, state_next;

    logic SignA_reg, SignA_next;
    logic SignB_reg, SignB_next;
    logic SignRes_reg, SignRes_next;
    logic AIsZero_reg, AIsZero_next;
    logic BIsZero_reg, BIsZero_next;
    logic AIsInf_reg, AIsInf_next;
    logic BIsInf_reg, BIsInf_next;
    logic AIsNan_reg, AIsNan_next;
    logic BIsNan_reg, BIsNan_next;
    logic AIsSub_reg, AIsSub_next;
    logic BIsSub_reg, BIsSub_next;
    logic [EXP_BITS-1:0] ExpA_reg, ExpA_next;
    logic [EXP_BITS-1:0] ExpB_reg, ExpB_next;
    logic [FRAC_BITS:0] MantA_reg, MantA_next;
    logic [FRAC_BITS:0] MantB_reg, MantB_next;
    logic signed [EXP_BITS+1:0] ExpRes_reg, ExpRes_next;
    logic BoothStart_reg, BoothStart_next;

    logic Overflow_reg, Overflow_next;
    logic Underflow_reg, Underflow_next;
    logic NaN_reg, NaN_next;
    logic Inf_reg, Inf_next;
    logic Zero_reg, Zero_next;
    logic [WIDTH-1:0] result_reg, result_next;

    logic BoothBusy, BoothDone;
    logic signed [((2*FRAC_BITS)+1):0] BoothProduct;

    function automatic logic [3:0] classify_value(
        input logic [EXP_BITS-1:0] exp,
        input logic [FRAC_BITS-1:0] frac
    );
        logic [3:0] classification;
        classification[0] = (exp == '0) && (frac == '0);
        classification[1] = (exp == {EXP_BITS{1'b1}}) && (frac == '0);
        classification[2] = (exp == {EXP_BITS{1'b1}}) && (frac != '0);
        classification[3] = (exp == '0) && (frac != '0);
        classify_value = classification;
    endfunction: classify_value

    function automatic logic [2:0] ExtractGRS
    (
        input logic [FRAC_BITS-1:0] mantissa
    );
        logic g;
        logic r;
        logic s;

        g = mantissa[FRAC_BITS-1];
        r = mantissa[FRAC_BITS-2];
        s = |mantissa[FRAC_BITS-3:0];
        ExtractGRS = {g, r, s};
    endfunction: ExtractGRS

    function automatic logic [WIDTH-1:0] Rounding
    (
        input logic [WIDTH-1:0] in,
        input logic g,
        input logic r,
        input logic s,
        input round_mode_t mode_in
    );
        logic lsb;
        logic tie;
        logic round_up;
        logic [FRAC_BITS:0] mantissa_in;
        logic [FRAC_BITS+1:0] mantissa_out;
        logic [EXP_BITS-1:0] exponent_in;
        logic [EXP_BITS-1:0] exponent_out;
        logic sign;
        logic inexact;

        sign = in[WIDTH-1];
        mantissa_in = {1'b1, in[FRAC_BITS-1:0]};
        exponent_in = in[WIDTH-2 -: EXP_BITS];
        exponent_out = exponent_in;
        lsb = mantissa_in[0];
        tie = g && !r && !s;
        inexact = g | r | s;
        round_up = 1'b0;

        case (mode_in)
            RNE: round_up = (g && (r || s)) || (tie && lsb);
            RTZ: round_up = 1'b0;
            RDN: round_up = inexact && sign;
            RUP: round_up = inexact && !sign;
            RMM: round_up = g;
            default: round_up = (g && (r || s)) || (tie && lsb);
        endcase

        if (round_up)
        begin
            mantissa_out = mantissa_in + 1'b1;
            if (mantissa_out == (1 << (FRAC_BITS + 1)))
            begin
                mantissa_out = mantissa_out >> 1;
                exponent_out = exponent_out + 1'b1;
            end
        end
        else
        begin
            mantissa_out = mantissa_in;
        end

        Rounding = {sign, exponent_out, mantissa_out[FRAC_BITS-1:0]};
    endfunction: Rounding

    always_ff @(posedge clk or negedge rst)
    begin
        if (!rst)
        begin
            a_reg <= '0;
            b_reg <= '0;
            round_mode_reg <= RNE;
            state_reg <= INIT_MUL;
            SignA_reg <= 1'b0;
            SignB_reg <= 1'b0;
            SignRes_reg <= 1'b0;
            AIsZero_reg <= 1'b0;
            BIsZero_reg <= 1'b0;
            AIsInf_reg <= 1'b0;
            BIsInf_reg <= 1'b0;
            AIsNan_reg <= 1'b0;
            BIsNan_reg <= 1'b0;
            AIsSub_reg <= 1'b0;
            BIsSub_reg <= 1'b0;
            ExpA_reg <= '0;
            ExpB_reg <= '0;
            MantA_reg <= '0;
            MantB_reg <= '0;
            ExpRes_reg <= '0;
            BoothStart_reg <= 1'b0;
            Overflow_reg <= 1'b0;
            Underflow_reg <= 1'b0;
            NaN_reg <= 1'b0;
            Inf_reg <= 1'b0;
            Zero_reg <= 1'b0;
            result_reg <= '0;
        end
        else
        begin
            a_reg <= a_next;
            b_reg <= b_next;
            round_mode_reg <= round_mode_next;
            state_reg <= state_next;
            SignA_reg <= SignA_next;
            SignB_reg <= SignB_next;
            SignRes_reg <= SignRes_next;
            AIsZero_reg <= AIsZero_next;
            BIsZero_reg <= BIsZero_next;
            AIsInf_reg <= AIsInf_next;
            BIsInf_reg <= BIsInf_next;
            AIsNan_reg <= AIsNan_next;
            BIsNan_reg <= BIsNan_next;
            AIsSub_reg <= AIsSub_next;
            BIsSub_reg <= BIsSub_next;
            ExpA_reg <= ExpA_next;
            ExpB_reg <= ExpB_next;
            MantA_reg <= MantA_next;
            MantB_reg <= MantB_next;
            ExpRes_reg <= ExpRes_next;
            BoothStart_reg <= BoothStart_next;
            Overflow_reg <= Overflow_next;
            Underflow_reg <= Underflow_next;
            NaN_reg <= NaN_next;
            Inf_reg <= Inf_next;
            Zero_reg <= Zero_next;
            result_reg <= result_next;
        end
    end

    always_comb
    begin: comb
        logic [3:0] class_a;
        logic [3:0] class_b;
        logic [EXP_BITS:0] shift_amt;
        logic [FRAC_BITS:0] mant_sub;
        logic signed [((2*FRAC_BITS)+1):0] mant_mult_norm;
        logic [2:0] grs_bits;
        logic [WIDTH-1:0] pre_round_result;
        logic [WIDTH-1:0] rounded_result;
        logic [3:0] result_class;

        a_next = a_reg;
        b_next = b_reg;
        round_mode_next = round_mode_reg;
        state_next = state_reg;
        SignA_next = SignA_reg;
        SignB_next = SignB_reg;
        SignRes_next = SignRes_reg;
        AIsZero_next = AIsZero_reg;
        BIsZero_next = BIsZero_reg;
        AIsInf_next = AIsInf_reg;
        BIsInf_next = BIsInf_reg;
        AIsNan_next = AIsNan_reg;
        BIsNan_next = BIsNan_reg;
        AIsSub_next = AIsSub_reg;
        BIsSub_next = BIsSub_reg;
        ExpA_next = ExpA_reg;
        ExpB_next = ExpB_reg;
        MantA_next = MantA_reg;
        MantB_next = MantB_reg;
        ExpRes_next = ExpRes_reg;
        BoothStart_next = 1'b0;
        Overflow_next = Overflow_reg;
        Underflow_next = Underflow_reg;
        NaN_next = NaN_reg;
        Inf_next = Inf_reg;
        Zero_next = Zero_reg;
        result_next = result_reg;

        class_a = '0;
        class_b = '0;
        shift_amt = '0;
        mant_sub = '0;
        mant_mult_norm = '0;
        grs_bits = '0;
        pre_round_result = '0;
        rounded_result = '0;
        result_class = '0;

        busy = (state_reg != IDLE_MUL) && (state_reg != DONE_MUL);
        done = (state_reg == DONE_MUL);
        Overflow = Overflow_reg;
        Underflow = Underflow_reg;
        NaN = NaN_reg;
        Inf = Inf_reg;
        Zero = Zero_reg;
        result = result_reg;

        case (state_reg)
            INIT_MUL:
            begin
                state_next = IDLE_MUL;
                Overflow_next = 1'b0;
                Underflow_next = 1'b0;
                NaN_next = 1'b0;
                Inf_next = 1'b0;
                Zero_next = 1'b0;
                result_next = '0;
                busy = 1'b0;
                done = 1'b0;
            end

            IDLE_MUL:
            begin
                Overflow_next = 1'b0;
                Underflow_next = 1'b0;
                NaN_next = 1'b0;
                Inf_next = 1'b0;
                Zero_next = 1'b0;
                result_next = '0;
                if (valid)
                begin
                    a_next = a;
                    b_next = b;
                    round_mode_next = round_mode;
                    state_next = SPLIT_MUL;
                end
            end

            SPLIT_MUL:
            begin
                SignA_next = a_reg[WIDTH-1];
                SignB_next = b_reg[WIDTH-1];
                ExpA_next = a_reg[WIDTH-2 -: EXP_BITS];
                ExpB_next = b_reg[WIDTH-2 -: EXP_BITS];
                class_a = classify_value(a_reg[WIDTH-2 -: EXP_BITS], a_reg[FRAC_BITS-1:0]);
                class_b = classify_value(b_reg[WIDTH-2 -: EXP_BITS], b_reg[FRAC_BITS-1:0]);
                {AIsSub_next, AIsNan_next, AIsInf_next, AIsZero_next} = class_a;
                {BIsSub_next, BIsNan_next, BIsInf_next, BIsZero_next} = class_b;
                SignRes_next = a_reg[WIDTH-1] ^ b_reg[WIDTH-1];

                if (class_a[2] || class_b[2] || ((class_a[1] || class_b[1]) && (class_a[0] || class_b[0])))
                begin
                    result_next = {1'b0, {EXP_BITS{1'b1}}, {1'b1, {(FRAC_BITS-1){1'b0}}}};
                    NaN_next = 1'b1;
                    Inf_next = 1'b0;
                    Zero_next = 1'b0;
                    Overflow_next = 1'b0;
                    Underflow_next = 1'b0;
                    state_next = DONE_MUL;
                end
                else if (class_a[1] || class_b[1])
                begin
                    result_next = {a_reg[WIDTH-1] ^ b_reg[WIDTH-1], {EXP_BITS{1'b1}}, {FRAC_BITS{1'b0}}};
                    NaN_next = 1'b0;
                    Inf_next = 1'b1;
                    Zero_next = 1'b0;
                    Overflow_next = 1'b0;
                    Underflow_next = 1'b0;
                    state_next = DONE_MUL;
                end
                else if (class_a[0] || class_b[0])
                begin
                    result_next = {a_reg[WIDTH-1] ^ b_reg[WIDTH-1], {EXP_BITS{1'b0}}, {FRAC_BITS{1'b0}}};
                    NaN_next = 1'b0;
                    Inf_next = 1'b0;
                    Zero_next = 1'b1;
                    Overflow_next = 1'b0;
                    Underflow_next = 1'b0;
                    state_next = DONE_MUL;
                end
                else
                begin
                    MantA_next = class_a[3] ? {1'b0, a_reg[FRAC_BITS-1:0]} : {1'b1, a_reg[FRAC_BITS-1:0]};
                    MantB_next = class_b[3] ? {1'b0, b_reg[FRAC_BITS-1:0]} : {1'b1, b_reg[FRAC_BITS-1:0]};
                    ExpRes_next = $signed({2'b00, a_reg[WIDTH-2 -: EXP_BITS]}) - BIAS + $signed({2'b00, b_reg[WIDTH-2 -: EXP_BITS]});
                    BoothStart_next = 1'b1;
                    state_next = MUL_MUL;
                end
            end

            MUL_MUL:
            begin
                if (BoothDone)
                begin
                    state_next = ROUND_MUL;
                end
            end

            ROUND_MUL:
            begin
                mant_mult_norm = BoothProduct;
                ExpRes_next = ExpRes_reg;

                if (mant_mult_norm[(2*FRAC_BITS)+1])
                begin
                    mant_mult_norm = mant_mult_norm >> 1;
                    ExpRes_next = ExpRes_reg + 1'b1;
                end

                if (ExpRes_next[EXP_BITS+1])
                begin
                    shift_amt = 1 - ExpRes_next;
                    mant_sub = mant_mult_norm[((2*FRAC_BITS)-1) -: FRAC_BITS] >> shift_amt;
                    if (!mant_sub)
                    begin
                        result_next = {SignRes_reg, {EXP_BITS{1'b0}}, {FRAC_BITS{1'b0}}};
                        Zero_next = 1'b1;
                        Underflow_next = 1'b1;
                    end
                    else
                    begin
                        result_next = {SignRes_reg, {EXP_BITS{1'b0}}, mant_sub[FRAC_BITS-1:0]};
                        Zero_next = 1'b0;
                        Underflow_next = 1'b0;
                    end
                    Overflow_next = 1'b0;
                    NaN_next = 1'b0;
                    Inf_next = 1'b0;
                end
                else if (ExpRes_next >= (2**EXP_BITS - 1))
                begin
                    result_next = {SignRes_reg, {EXP_BITS{1'b1}}, {FRAC_BITS{1'b0}}};
                    Overflow_next = 1'b1;
                    Inf_next = 1'b1;
                    Underflow_next = 1'b0;
                    NaN_next = 1'b0;
                    Zero_next = 1'b0;
                end
                else
                begin
                    grs_bits = ExtractGRS(mant_mult_norm[FRAC_BITS-1:0]);
                    pre_round_result = {SignRes_reg, ExpRes_next[EXP_BITS-1:0], mant_mult_norm[((2*FRAC_BITS)-1) -: FRAC_BITS]};
                    rounded_result = Rounding(pre_round_result, grs_bits[2], grs_bits[1], grs_bits[0], round_mode_reg);
                    result_class = classify_value(rounded_result[WIDTH-2 -: EXP_BITS], rounded_result[FRAC_BITS-1:0]);
                    result_next = rounded_result;
                    Overflow_next = 1'b0;
                    Underflow_next = 1'b0;
                    NaN_next = result_class[2];
                    Inf_next = result_class[1];
                    Zero_next = result_class[0];
                end
                state_next = DONE_MUL;
            end

            DONE_MUL:
            begin
                state_next = IDLE_MUL;
            end

            default:
            begin
                state_next = IDLE_MUL;
            end
        endcase
    end: comb

    booth_multiplier_seq #(
        .FRAC_BITS(FRAC_BITS),
        .STAGES(STAGES)
    ) booth_multiplier_inst (
        .clk(clk),
        .rst(rst),
        .start(BoothStart_reg),
        .mant_a(MantA_reg),
        .mant_b(MantB_reg),
        .busy(BoothBusy),
        .done(BoothDone),
        .product(BoothProduct)
    );

endmodule: flp_mul

module booth_multiplier_seq
#(
    parameter int FRAC_BITS = 23,
    parameter int STAGES    = 4
)(
    input  logic clk,
    input  logic rst,
    input  logic start,
    input  logic [FRAC_BITS:0] mant_a,
    input  logic [FRAC_BITS:0] mant_b,
    output logic busy,
    output logic done,
    output logic signed [((2*FRAC_BITS)+1):0] product
);

    localparam int TOTAL_ITERS = FRAC_BITS + 2;
    localparam int ITER_BITS = (TOTAL_ITERS > 1) ? $clog2(TOTAL_ITERS + 1) : 1;
    localparam int ITERS_PER_CYCLE = (TOTAL_ITERS + STAGES - 1) / STAGES;

    logic busy_reg, busy_next;
    logic done_reg, done_next;
    logic [FRAC_BITS:0] mant_a_reg, mant_a_next;
    logic [FRAC_BITS:0] mant_b_reg, mant_b_next;
    logic signed [((2*FRAC_BITS)+1):0] acc_reg, acc_next;
    logic [ITER_BITS-1:0] iter_reg, iter_next;
    logic signed [((2*FRAC_BITS)+1):0] product_reg, product_next;

    always_ff @(posedge clk or negedge rst)
    begin
        if (!rst)
        begin
            busy_reg <= 1'b0;
            done_reg <= 1'b0;
            mant_a_reg <= '0;
            mant_b_reg <= '0;
            acc_reg <= '0;
            iter_reg <= '0;
            product_reg <= '0;
        end
        else
        begin
            busy_reg <= busy_next;
            done_reg <= done_next;
            mant_a_reg <= mant_a_next;
            mant_b_reg <= mant_b_next;
            acc_reg <= acc_next;
            iter_reg <= iter_next;
            product_reg <= product_next;
        end
    end

    always_comb
    begin: comb
        logic signed [((2*FRAC_BITS)+1):0] acc_work;
        logic signed [((2*FRAC_BITS)+1):0] multiplicand_used;
        logic [FRAC_BITS+2:0] multiplier_used;
        logic [ITER_BITS-1:0] iter_work;

        busy_next = busy_reg;
        done_next = 1'b0;
        mant_a_next = mant_a_reg;
        mant_b_next = mant_b_reg;
        acc_next = acc_reg;
        iter_next = iter_reg;
        product_next = product_reg;

        acc_work = acc_reg;
        multiplicand_used = {{(FRAC_BITS+1){1'b0}}, mant_a_reg};
        multiplier_used = {1'b0, mant_b_reg, 1'b0};
        iter_work = iter_reg;

        if (start && !busy_reg)
        begin
            mant_a_next = mant_a;
            mant_b_next = mant_b;
            acc_next = '0;
            iter_next = '0;
            product_next = '0;
            busy_next = 1'b1;
        end
        else if (busy_reg)
        begin
            for (int i = 0; i < ITERS_PER_CYCLE; i++)
            begin
                if (iter_work < TOTAL_ITERS)
                begin
                    case ({multiplier_used[iter_work+1], multiplier_used[iter_work]})
                        2'b01: acc_work = acc_work + (multiplicand_used <<< iter_work);
                        2'b10: acc_work = acc_work - (multiplicand_used <<< iter_work);
                        default: acc_work = acc_work;
                    endcase
                    iter_work = iter_work + 1'b1;
                end
            end

            acc_next = acc_work;
            iter_next = iter_work;

            if (iter_work >= TOTAL_ITERS)
            begin
                product_next = acc_work;
                busy_next = 1'b0;
                done_next = 1'b1;
            end
        end
    end: comb

    assign busy = busy_reg;
    assign done = done_reg;
    assign product = product_reg;

endmodule: booth_multiplier_seq
