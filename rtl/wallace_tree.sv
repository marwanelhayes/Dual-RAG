// =============================================================================
// wallace_tree.sv
// -----------------------------------------------------------------------------
// Wallace tree multiplier for fast multiplication in ALU.
// =============================================================================
import shared_pkg::*;

module wallace_tree #(
    parameter int WIDTH = 32
) (
    input   logic signed [WIDTH-1:0]            A,
    input   logic signed [WIDTH-1:0]            B,
    input   mul_sel_t                           sel,
    output  logic signed [2*WIDTH-1:0]          P
);
    // Partial products as shifted versions of 'a' gated by bits of 'b'
    logic [2*WIDTH-1:0] ops     [WIDTH];
    logic [2*WIDTH-1:0] next_ops[WIDTH];
    logic [2*WIDTH-1:0] product;
    logic [WIDTH-1:0]     multiplicand, multiplier;
    logic [1:0] remainder;
    logic MulSign;
    int n , level , out_idx;

    // 3:2 compressor implemented via carry-save adder (bitwise)
    function logic [2*WIDTH-1:0] csa_sum (
        input logic [2*WIDTH-1:0] x,
        input logic [2*WIDTH-1:0] y,
        input logic [2*WIDTH-1:0] z
    );
        csa_sum = x ^ y ^ z;
    endfunction

    function logic [2*WIDTH-1:0] csa_carry (
        input logic [2*WIDTH-1:0] x,
        input logic [2*WIDTH-1:0] y,
        input logic [2*WIDTH-1:0] z
    );
        csa_carry = ((x & y) | (x & z) | (y & z)) << 1; // carry shifted left
    endfunction

    // Specialized remainder function for division by 3
    // Gets the remainder when dividing by 3 using non-restoring division
    function logic [1:0] rem_by3
    (
        input logic [WIDTH-1:0] dividend
    );
        logic [WIDTH:0] A;
        logic [WIDTH-1:0] Q;
        int i;

        begin
            A = '0;
            Q = dividend;

            // Non-restoring division loop specialized for divisor = 3
            for (i = 0; i < WIDTH; i++) 
            begin
                if (A[WIDTH]) 
                begin
                    A = {A[WIDTH-1:0], Q[WIDTH-1]};
                    A = A + 3;   // add constant divisor
                end
                else 
                begin
                    A = {A[WIDTH-1:0], Q[WIDTH-1]};
                    A = A - 3;   // subtract constant divisor
                end
                Q = {Q[WIDTH-2:0], ~A[WIDTH]};
            end

            // Final restoration if negative
            if (A[WIDTH])
                A = A + 3;

            // The remainder is just the lower bits of A
            rem_by3 = A[1:0];  // remainder fits in 2 bits (0,1,2)
        end
    endfunction


    always_comb 
    begin
        MulSign = A[WIDTH-1] ^ B[WIDTH-1];
        multiplicand = '0;
        multiplier   = '0;

        case(sel)
            unsign: 
            begin
                multiplicand = A;
                multiplier   = B;
            end
            sign: 
            begin
                if(A[WIDTH-1])
                    multiplicand = -A;
                else
                    multiplicand = A;
                if(B[WIDTH-1])
                    multiplier   = -B;
                else
                    multiplier   = B;
            end
            signXunsign: 
            begin
                if(A[WIDTH-1])
                    multiplicand = -A;
                else
                    multiplicand = A;
                multiplier   = B;
            end
            error:
            begin
                multiplicand = '0;
                multiplier   = '0;
            end
        endcase
        
        // Generate partial products
        foreach(ops[j]) 
        begin: gen_pp
            ops[j] = multiplier[j] ? ({{WIDTH{1'b0}}, multiplicand} << j) : '0;
        end:gen_pp

        // Iterative Wallace reduction (compress groups of 3 via CSA)
        n = WIDTH; // number of current operands
  
        for (level = 0; ((level < WIDTH) && (n > 2)); level++) 
        begin: wallace_level

            out_idx = 0;

            // Compress triplets
            for (int i = 0; ((i + 2) < n); i = i+3) 
            begin: compression
                next_ops[out_idx + 0] = csa_sum  (ops[i], ops[i+1], ops[i+2]);
                next_ops[out_idx + 1] = csa_carry(ops[i], ops[i+1], ops[i+2]);
                out_idx += 2;
            end: compression

            remainder = rem_by3(n);

            // Pass through leftovers (0,1,2 operands)
            if(remainder == 1) 
            begin: leftovers
                next_ops[out_idx] = ops[n-1];
                out_idx += 1;
            end: leftovers
            else if(remainder == 2) 
            begin: leftovers2
                next_ops[out_idx + 0] = ops[n-2];
                next_ops[out_idx + 1] = ops[n-1];
                out_idx += 2;
            end: leftovers2

            // Prepare next stage
            for (int k = 0; k < out_idx; k++)
            begin: copy 
                ops[k] = next_ops[k];
            end: copy
            n = out_idx;
        end: wallace_level

        // Final addition
        if (n == 0) 
        begin
            product = '0;
        end
        else if (n == 1) 
        begin
            product = ops[0];
        end 
        else 
        begin
            product = ops[0] + ops[1];
        end

        // Adjust sign if needed
        if ((sel == sign)) 
        begin
            if(MulSign) 
                P = -product;
            else 
                P = product;
        end 
        else if ((sel == signXunsign)) 
        begin
            if(A[WIDTH-1]) 
                P = -product;
            else 
                P = product;
        end 
        else
        begin
            P = product;
        end
    end
endmodule
