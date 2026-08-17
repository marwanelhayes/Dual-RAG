// =============================================================================
// lzc_wr.sv
// -----------------------------------------------------------------------------
// Leading zero counter for normalization in FPU.
// =============================================================================
module lzc_wr #(
    parameter int WIDTH = 24 // You can change this to ANY integer (e.g., 23, 24, 27, 53)
)(
    input  logic [WIDTH-1:0]       A_in,
    // Output size is automatically calculated to hold 'WIDTH'
    output logic [$clog2(WIDTH)-1:0] leading_zeros, 
    output logic                   is_zero
);

    // 1. Calculate the next perfect power of 2 (e.g., 24 -> 32)
    localparam int PWR2_W = 1 << $clog2(WIDTH); 
    
    logic [PWR2_W-1:0]          padded_A;
    logic [$clog2(PWR2_W)-1:0]  tree_count;
    logic                       tree_valid;

    // 2. Safe Zero Padding
    // We do NOT use the replication operator {N{1'b0}} because if N=0 (e.g., WIDTH=32), 
    // it causes a syntax error. This approach is universally synthesizable.
    always_comb begin
        padded_A = '0; // Default the whole power-of-2 vector to zeros
        // Drop the actual input into the Most Significant Bits
        padded_A[PWR2_W-1 : PWR2_W-WIDTH] = A_in; 
    end

    // 3. Instantiate the strict Power-of-2 Core
    lzc #(
        .WIDTH(PWR2_W)
    ) core_tree (
        .A_in  (padded_A),
        .Z_out (tree_count),
        .V_flag(tree_valid)
    );

    // 4. Format outputs for the Floating Point Unit
    always_comb begin
        is_zero = ~tree_valid;
        
        if (is_zero) begin
            leading_zeros = WIDTH; // If empty, the count is exactly WIDTH
        end else begin
            leading_zeros = tree_count[$clog2(WIDTH)-1:0]; // Truncate to needed bits
        end
    end

endmodule




module lzc #(
    parameter int WIDTH = 32,
    // Automatically calculate the number of bits needed for the count
    parameter int COUNT_WIDTH = $clog2(WIDTH) 
)(
    input  logic [WIDTH-1:0]       A_in,  // Input operand A 
    output logic [COUNT_WIDTH-1:0] Z_out, // Leading zero count (Z bits) [cite: 35]
    output logic                   V_flag // Valid flag (1 = has a '1', 0 = all zeros) 
                                          // Note: The paper defines V as the all-zero flag[cite: 35], 
                                          // but for tree logic, a 'Valid' flag is easier to route. 
                                          // We will invert it at the top level if needed.
);

    generate
        // -----------------------------------------------------------------
        // BASE CASE 1: 1-Bit Wide
        // -----------------------------------------------------------------
        if (WIDTH == 1) 
        begin : gen_base_1
            assign Z_out = '0;
            assign V_flag = A_in[0];
        end 
        
        // -----------------------------------------------------------------
        // BASE CASE 2: 2-Bit Wide (The building block)
        // -----------------------------------------------------------------
        else if (WIDTH == 2) 
        begin : gen_base_2
            // Concurrent evaluation of the 2 bits 
            assign V_flag = A_in[1] | A_in[0]; 
            assign Z_out  = ~A_in[1];          // If MSB is 0, count is 1. Else 0.
        end 
        
        // -----------------------------------------------------------------
        // RECURSIVE STEP: N-Bit Wide
        // -----------------------------------------------------------------
        else 
        begin : gen_tree
            // Split the input into Left (Upper) and Right (Lower) halves
            localparam int HALF_WIDTH = WIDTH / 2;
            
            logic [$clog2(HALF_WIDTH)-1:0] Z_left;
            logic                          V_left;
            
            logic [$clog2(HALF_WIDTH)-1:0] Z_right;
            logic                          V_right;
            
            // Instantiate Left Half (Most Significant Bits)
            lzc #(
                .WIDTH(HALF_WIDTH)
            ) left_node (
                .A_in(A_in[WIDTH-1 : HALF_WIDTH]),
                .Z_out(Z_left),
                .V_flag(V_left)
            );
            
            // Instantiate Right Half (Least Significant Bits)
            lzc #(
                .WIDTH(HALF_WIDTH)
            ) right_node (
                .A_in(A_in[HALF_WIDTH-1 : 0]),
                .Z_out(Z_right),
                .V_flag(V_right)
            );
            
            // -------------------------------------------------------------
            // The Proposed Concurrent Combination Logic 
            // Instead of evaluating intermediate strings, we multiplex the 
            // Z bits directly based on the Valid flag of the upper half.
            // -------------------------------------------------------------
            assign V_flag = V_left | V_right;
            
            always_comb 
            begin
                if (V_left) 
                begin
                    // If the left (upper) half has a 1, the MSB of the count is 0, 
                    // and we use the left half's count.
                    Z_out = {1'b0, Z_left};
                end 
                else 
                begin
                    // If the left half is all 0s, the MSB of the count is 1, 
                    // and we use the right half's count.
                    Z_out = {1'b1, Z_right};
                end
            end
        end
    endgenerate

endmodule