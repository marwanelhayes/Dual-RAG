// =============================================================================
// cache.sv
// -----------------------------------------------------------------------------
// Parameterized cache with configurable WAY.
//
// WAY model:
//   - WAY = 1                 -> direct-mapped cache
//   - 1 < WAY < TOTAL_LINES           -> set-WAYiative cache
//   - WAY = TOTAL_LINES               -> fully WAYiative cache
//
// Geometry defaults:
//   TOTAL_LINES       = 32
//   WAY        = 1
//   SETS        = TOTAL_LINES         / WAY
//   LINE_WORDS  = 4
//   DATA_WIDTH  = 32
//   Line size   = 4 words x 4 bytes = 16 bytes
//   Capacity    = TOTAL_LINES   x LINE_WORDS x DATA_WIDTH/8
//               = 32 x 4 x 4 = 512 bytes by default
//
// Data cache policy: write-back, write-allocate
//   - Read hit:    return word immediately
//   - Read miss:   AXI burst read, allocate/fill selected victim way
//   - Write hit:   update cache word, AXI single write
//   - Write miss:  AXI single write only, line allocation
//
// Replacement policy for WAY > 1:
//   - invalid way first
//   - otherwise last recently used per set
//
// I-cache: set READ_ONLY=1 to ignore/synthesize away write behavior.
// =============================================================================

import shared_pkg::*;

module cache
#(
    parameter int  DATA_WIDTH   = 32,
    parameter int  ADDR_WIDTH   = 32,
    parameter int  TOTAL_LINES  = 32,                         // total cache lines, power of 2 recommended. This represents the cache capacity for lines of words. Multiply this value by LINE_WORDS to get the number of words stored in cache
    parameter int  WAY          = 1,                          // 1=direct, TOTAL_LINES =fully WAY
    parameter int  LINE_WORDS   = 4,                          // words per cache line, power of 2
    parameter bit  READ_ONLY    = 1'b0,                       // 1 for I-cache
    parameter int  AXI_SIZE     = 4
)
(
    input  logic                  clk,
    input  logic                  rst,

    // ── CPU-side interface ────────────────────────────────────────────────────
    input  logic [ADDR_WIDTH-1:0] CPUAddr,
    input  logic [DATA_WIDTH-1:0] CPUWriteData,
    input  logic                  CPUWriteEn,
    input  logic                  CPUReadEn,
    output logic [DATA_WIDTH-1:0] CPUReadData,
    output logic                  CacheHit,     // 0 = pipeline must stall

    // ── AXI4 master interface: write address channel ─────────────────────────
    output logic [ADDR_WIDTH-1:0] MAWAddr,
    output logic [7:0]            MAWLen,
    output logic [AXI_SIZE-1:0]   MAWSize,
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
    output logic [AXI_SIZE-1:0]   MARSize,
    output axi_burst_t            MARBurst,
    output logic                  MARValid,
    input  logic                  MARReady,

    // ── AXI4 master interface: read data channel ─────────────────────────────
    output logic                  MRReady,
    input  logic [DATA_WIDTH-1:0] MRRData,
    input  axi_resp_t             MRRResp,
    input  logic                  MRRLast,
    input  logic                  MRRValid
);


    localparam int DATA_BYTES  = DATA_WIDTH / 8;
    localparam int SETS        = TOTAL_LINES / WAY;
    localparam int LINE_BYTES  = LINE_WORDS * DATA_BYTES;
    localparam int TRANSFER_SIZE = $clog2(DATA_BYTES); // AXI size encoding (0=1B, 1=2B, 2=4B, ...)

    localparam int BYTE_OFF_W  = (DATA_BYTES  > 1)  ? $clog2(DATA_BYTES)  : 0;
    localparam int OFF_W       = (LINE_BYTES  > 1)  ? $clog2(LINE_BYTES)  : 0;
    localparam int IDX_W_RAW   = (SETS        > 1)  ? $clog2(SETS)        : 0;
    localparam int IDX_W       = (IDX_W_RAW   > 0)  ? IDX_W_RAW           : 1;
    localparam int WAY_W       = (WAY         > 1)  ? $clog2(WAY)         : 1;
    localparam int WORD_W_RAW  = (LINE_WORDS  > 1)  ? $clog2(LINE_WORDS)  : 0;
    localparam int WORD_W      = (WORD_W_RAW  > 0)  ? WORD_W_RAW          : 1;
    localparam int TAG_W       = ADDR_WIDTH - OFF_W - IDX_W_RAW;

    logic valid_reg [SETS][WAY];  // valid bits
    logic valid [SETS][WAY];
    logic [TAG_W-1:0] tags_reg [SETS][WAY];  // tags
    logic [TAG_W-1:0] tags [SETS][WAY];
    logic [DATA_WIDTH-1:0] data_reg [SETS][WAY][LINE_WORDS];  // Data arrays
    logic [DATA_WIDTH-1:0] data [SETS][WAY][LINE_WORDS];
    logic [WORD_W-1:0] counter_reg; // for tracking burst progress
    logic [WORD_W-1:0] counter;
    logic [WAY_W-1:0] lru_way_empty_reg [SETS]; // for replacement logic
    logic [WAY_W-1:0] lru_way_empty [SETS];
    logic pending_write;
    logic [DATA_WIDTH-1:0] pending_write_data;
    logic [ADDR_WIDTH-1:0] miss_line_addr;
    logic [WAY_W-1:0] hit_way;
    logic [WAY_W-1:0] victim_way;
    logic [WAY_W-1:0] victim_way_reg;
    logic [WAY-1:0] hit_array;

    logic [IDX_W-1:0] CpuIdx;
    logic [TAG_W-1:0] CpuTag;
    logic [OFF_W-1:0] CpuOffset;


    logic [IDX_W-1:0] CpuIdx_reg;
    logic [TAG_W-1:0] CpuTag_reg;
    logic [OFF_W-1:0] CpuOffset_reg;

    logic read_through_hit_reg;
    logic read_through_hit;
    logic refill_error_reg;
    logic refill_error;

    logic [ADDR_WIDTH-1:0] MAWAddr_next;
    logic [7:0]            MAWLen_next;
    logic [2:0]            MAWSize_next;
    axi_burst_t            MAWBurst_next;
    logic                  MAWValid_next;
    logic [DATA_WIDTH-1:0] MWData_next;
    logic [(DATA_WIDTH/8)-1:0] MWStrb_next;
    logic                  MWLast_next;
    logic                  MWValid_next;

    logic                  MBReady_next;
    logic [ADDR_WIDTH-1:0] MARAddr_next;
    logic [7:0]            MARLen_next;
    logic [2:0]            MARSize_next;
    axi_burst_t            MARBurst_next;
    logic                  MARValid_next;

    logic [3:0] lru_counter [SETS][WAY];
    logic [WAY_W-1:0] lru_way [SETS];
    logic [WAY_W-1:0] lru_min_way;

    logic and_valid_bits;


    axi_state_t state, next_state;

    always_comb
    begin
        CpuOffset = CPUAddr[OFF_W-1:0];
        if(WAY == TOTAL_LINES)
        begin
            CpuIdx = '0;
            CpuTag = CPUAddr[ADDR_WIDTH-1:OFF_W];
        end
        else
        begin
            CpuIdx = CPUAddr[OFF_W+IDX_W-1:OFF_W];
            CpuTag = CPUAddr[ADDR_WIDTH-1:OFF_W+IDX_W];
        end
    end


    always_comb
    begin
        hit_array = '0;
        hit_way = '0;
        victim_way = lru_way[CpuIdx];

        for(int i = 0; i < WAY; i++)
        begin
            if(valid_reg[CpuIdx][i] && (tags_reg[CpuIdx][i] == CpuTag))
            begin
                hit_array[i] = 1'b1;
                hit_way = WAY_W'(i);
            end
        end

        for(int i = WAY - 1; i >= 0; i--)
        begin
            if(!valid_reg[CpuIdx][i])
            begin
                victim_way = WAY_W'(i);
            end
        end
    end

    always_comb
    begin
        and_valid_bits = 1'b1;
        for(int i = 0; i < WAY; i++)
        begin
            and_valid_bits &= valid_reg[CpuIdx_reg][i];
        end
    end

    generate
        if(WAY == 1)
        begin : gen_direct_mapped
            always_comb
            begin
                if(CPUReadEn || CPUWriteEn)
                begin
                    if((|hit_array) && (state == AXI_IDLE))
                    begin
                        CacheHit = 1'b1;
                    end
                    else if(read_through_hit_reg)
                    begin
                        CacheHit = 1'b1;
                    end
                    else
                    begin
                        CacheHit = 1'b0;
                    end
                end
                else
                begin
                    CacheHit = 1'b1;
                end
            end

            always_comb
            begin
                if(read_through_hit_reg)
                begin
                    CPUReadData = data_reg[CpuIdx_reg][0][CpuOffset_reg[OFF_W-1 : 2]];
                end
                else if(CPUWriteEn && CacheHit)
                begin
                    CPUReadData = CPUWriteData;
                end
                else if(CacheHit)
                begin
                    CPUReadData = data_reg[CpuIdx][0][CpuOffset[OFF_W-1 : 2]];
                end
                else
                begin
                    CPUReadData = '0;
                end
            end
        end:gen_direct_mapped
        else
        begin:gen_set_associative
            always_comb
            begin
                if(CPUReadEn || CPUWriteEn)
                begin
                    if(|hit_array && (state == AXI_IDLE))
                    begin
                        CacheHit = 1'b1;
                    end
                    else if(read_through_hit_reg)
                    begin
                        CacheHit = 1'b1;
                    end
                    else
                    begin
                        CacheHit = 1'b0;
                    end
                end
                else
                begin
                    CacheHit = 1'b1;
                end
            end

            always_comb
            begin
                CPUReadData = '0;
                for(int i = 0; i < WAY; i++)
                begin
                    if(hit_array[i] && CacheHit)
                    begin
                        if(CPUWriteEn)
                        begin
                            CPUReadData = CPUWriteData;
                        end
                        else
                        begin
                            CPUReadData = data_reg[CpuIdx][i][CpuOffset[OFF_W-1 : 2]];
                        end
                    end
                    else if(read_through_hit_reg)
                    begin
                        CPUReadData = data_reg[CpuIdx_reg][victim_way_reg][CpuOffset_reg[OFF_W-1 : 2]];
                    end
                end
            end

            always_ff @(posedge clk or negedge rst)
            begin
                if(!rst)
                begin
                    foreach (lru_counter[i, j]) lru_counter[i][j] <= '0;
                end
                else
                begin
                    foreach(hit_array[i])
                    begin
                        if(hit_array[i])
                        begin
                            if(lru_counter[CpuIdx][i] < 15)
                                lru_counter[CpuIdx][i] <= lru_counter[CpuIdx][i] + 1'b1;
                        end
                        else
                        begin
                            if(lru_counter[CpuIdx][i] > 0)
                                lru_counter[CpuIdx][i] <= lru_counter[CpuIdx][i] - 1'b1;
                        end
                    end
                end
            end

            // Least-recently-used way = way with the minimum counter (lowest
            // index wins on a tie). Computed combinationally, registered below.
            always_comb
            begin
                lru_min_way = '0;
                for(int i = 1; i < WAY; i++)
                begin
                    if(lru_counter[CpuIdx][i] < lru_counter[CpuIdx][lru_min_way])
                        lru_min_way = WAY_W'(i);
                end
            end

            always_ff @(negedge clk or negedge rst)
            begin
                if(!rst)
                begin
                    foreach (lru_way[i]) lru_way[i] <= '0;
                end
                else
                begin
                    lru_way[CpuIdx] <= lru_min_way;
                end
            end
        end:gen_set_associative
    endgenerate

    always_ff @(posedge clk or negedge rst)
    begin
        if(!rst)
        begin
            CpuIdx_reg <= '0;
            CpuTag_reg <= '0;
            CpuOffset_reg <= '0;
            pending_write <= 1'b0;
            pending_write_data <= '0;
            miss_line_addr <= '0;
            victim_way_reg <= '0;
        end
        else
        begin
            if((state == AXI_IDLE) && (CPUReadEn || CPUWriteEn) && !CacheHit)
            begin
                CpuOffset_reg <= CpuOffset;
                pending_write <= CPUWriteEn;
                pending_write_data <= CPUWriteData;
                miss_line_addr <= {CPUAddr[ADDR_WIDTH-1:OFF_W], {OFF_W{1'b0}}};
                victim_way_reg <= victim_way;
                if(WAY == TOTAL_LINES)
                begin
                    CpuIdx_reg <= '0;
                    CpuTag_reg <= CpuTag;
                end
                else
                begin
                    CpuIdx_reg <= CpuIdx;
                    CpuTag_reg <= CpuTag;
                end
            end
        end
    end

    always_ff @(posedge clk or negedge rst)
    begin
        if(!rst)
        begin
            state <= AXI_IDLE;
            foreach (valid_reg[i, j]) valid_reg[i][j] <= 1'b0;
            foreach (tags_reg[i, j]) tags_reg[i][j] <= '0;
            foreach (data_reg[i, j, k]) data_reg[i][j][k] <= '0;
            foreach (lru_way_empty_reg[i]) lru_way_empty_reg[i] <= '0;
        end
        else
        begin
            state <= next_state;
            foreach (valid_reg[i, j]) valid_reg[i][j] <= valid[i][j];
            foreach (tags_reg[i, j]) tags_reg[i][j] <= tags[i][j];
            foreach (data_reg[i, j, k]) data_reg[i][j][k] <= data[i][j][k];
            foreach (lru_way_empty_reg[i]) lru_way_empty_reg[i] <= lru_way_empty[i];
        end
    end

    always_ff @(posedge clk or negedge rst)
    begin
        if(!rst)
        begin
            MAWAddr <= '0;
            MAWLen <= '0;
            MAWSize <= '0;
            MAWBurst <= FIXED;
            MAWValid <= 1'b0;
            MWData <= '0;
            MWStrb <= '0;
            MWLast <= 1'b0;
            MWValid <= 1'b0;
            MBReady <= 1'b0;
            MARAddr <= '0;
            MARLen <= '0;
            MARSize <= '0;
            MARBurst <= FIXED;
            MARValid <= 1'b0;
            MRReady <= 1'b0;
            counter_reg <= '0;
            read_through_hit_reg <= 1'b0;
            refill_error_reg <= 1'b0;
        end
        else
        begin
            MAWAddr <= MAWAddr_next;
            MAWLen <= MAWLen_next;
            MAWSize <= MAWSize_next;
            MAWBurst <= MAWBurst_next;
            MAWValid <= MAWValid_next;
            MWData <= MWData_next;
            MWStrb <= MWStrb_next;
            MWLast <= MWLast_next;
            MWValid <= MWValid_next;
            MBReady <= MBReady_next;
            MARAddr <= MARAddr_next;
            MARLen <= MARLen_next;
            MARSize <= MARSize_next;
            MARBurst <= MARBurst_next;
            MARValid <= MARValid_next;
            MRReady <= 1'b1;
            counter_reg <= counter;
            read_through_hit_reg <= read_through_hit;
            refill_error_reg <= refill_error;
        end
    end

    generate
        if(READ_ONLY)
        begin:gen_read_only_cache
            always_comb
            begin
                next_state = state;
                valid = valid_reg;
                tags = tags_reg;
                data = data_reg;
                MAWAddr_next = MAWAddr;
                MAWLen_next = MAWLen;
                MAWSize_next = MAWSize;
                MAWBurst_next = MAWBurst;
                MAWValid_next = MAWValid;
                MWData_next = MWData;
                MWStrb_next = MWStrb;
                MWLast_next = MWLast;
                MWValid_next = MWValid;
                MBReady_next = MBReady;
                MARAddr_next = MARAddr;
                MARLen_next = MARLen;
                MARSize_next = MARSize;
                MARBurst_next = MARBurst;
                MARValid_next = MARValid;
                counter = counter_reg;
                lru_way_empty = lru_way_empty_reg;
                read_through_hit = read_through_hit_reg;
                refill_error = refill_error_reg;

                case(state)
                    AXI_IDLE:
                    begin
                        if(CPUReadEn && !CacheHit)
                        begin
                            next_state = AXI_RD_ADDR;
                            MARValid_next = 1'b1;
                            MARAddr_next = {CPUAddr[ADDR_WIDTH-1:OFF_W], {OFF_W{1'b0}}};
                            MARLen_next = LINE_WORDS - 1;
                            MARSize_next = TRANSFER_SIZE;
                            MARBurst_next = INCR;
                            counter = '0;
                            read_through_hit = 1'b0;
                            refill_error = 1'b0;
                            MAWAddr_next = 'b0;
                            MAWLen_next = 'b0;
                            MAWSize_next = 'b0;
                            MAWBurst_next = FIXED;
                            MAWValid_next = 'b0;
                            MWData_next = 'b0;
                            MWStrb_next = 'b0;
                            MWLast_next = 'b0;
                            MWValid_next = 'b0;
                            MBReady_next = 'b0;
                        end
                        else
                        begin
                            next_state = AXI_IDLE;
                            MAWValid_next = 1'b0;
                            MARAddr_next = '0;
                            MARValid_next = 1'b0;
                            MARLen_next = '0;
                            MARSize_next = '0;
                            MARBurst_next = INCR;
                            counter = '0;
                            read_through_hit = 1'b0;
                            refill_error = 1'b0;
                            MAWAddr_next = 'b0;
                            MAWLen_next = 'b0;
                            MAWSize_next = 'b0;
                            MAWBurst_next = FIXED;
                            MAWValid_next = 'b0;
                            MWData_next = 'b0;
                            MWStrb_next = 'b0;
                            MWLast_next = 'b0;
                            MWValid_next = 'b0;
                            MBReady_next = 'b0;
                        end
                    end
                    AXI_RD_ADDR:
                    begin
                        if(MARReady && MARValid)
                        begin
                            next_state = AXI_RD_DATA;
                            MARValid_next = 1'b0;
                            MAWValid_next = 1'b0;
                            MARAddr_next = '0;
                            MARValid_next = 1'b0;
                            MARLen_next = '0;
                            MARSize_next = '0;
                            MARBurst_next = INCR;
                            counter = '0;
                            read_through_hit = 1'b0;
                        end
                    end

                    AXI_RD_DATA:
                    begin
                        if(WAY == 1)
                        begin:direct_mapped_read
                            if(MRRValid && MRReady)
                            begin
                                if(MRRResp == AXI_OKAY)
                                begin
                                    data[CpuIdx_reg][0][counter] = MRRData;
                                end
                                else
                                begin
                                    refill_error = 1'b1;
                                end
                                if((MRRResp == AXI_OKAY) && (counter == (CpuOffset_reg[OFF_W-1:2])))
                                begin
                                    read_through_hit = 1'b1;
                                end
                                else
                                begin
                                    read_through_hit = 1'b0;
                                end
                                counter = counter + 1'b1;
                                if(MRRLast)
                                begin
                                    if((MRRResp == AXI_OKAY) && !refill_error_reg)
                                    begin
                                        valid[CpuIdx_reg][0] = 1'b1;
                                        tags[CpuIdx_reg][0] = CpuTag_reg;
                                    end
                                    next_state = AXI_IDLE;
                                    counter = '0;
                                end
                            end
                        end:direct_mapped_read
                        else
                        begin:set_associative_read
                            if(MRRValid && MRReady)
                            begin
                                if(MRRResp == AXI_OKAY)
                                begin
                                    data[CpuIdx_reg][victim_way_reg][counter] = MRRData;
                                end
                                else
                                begin
                                    refill_error = 1'b1;
                                end
                                if((MRRResp == AXI_OKAY) && (counter == (CpuOffset_reg[OFF_W-1:2])))
                                begin
                                    read_through_hit = 1'b1;
                                end
                                else
                                begin
                                    read_through_hit = 1'b0;
                                end
                                counter = counter + 1'b1;

                                if(MRRLast)
                                begin
                                    if((MRRResp == AXI_OKAY) && !refill_error_reg)
                                    begin
                                        valid[CpuIdx_reg][victim_way_reg] = 1'b1;
                                        tags[CpuIdx_reg][victim_way_reg] = CpuTag_reg;
                                    end
                                    next_state = AXI_IDLE;
                                    MAWValid_next = 1'b0;
                                    MARAddr_next = '0;
                                    MARValid_next = 1'b0;
                                    MARLen_next = '0;
                                    MARSize_next = '0;
                                    MARBurst_next = INCR;
                                    counter = '0;
                                    if(!and_valid_bits)
                                    begin
                                        lru_way_empty[CpuIdx_reg] = lru_way_empty[CpuIdx_reg] + 1'b1;
                                    end
                                end
                            end
                        end:set_associative_read
                    end
                endcase
            end
        end:gen_read_only_cache

        else
        begin:gen_read_write_cache

            logic dirty_reg [SETS][WAY];
            logic dirty [SETS][WAY];

            always_ff @(posedge clk or negedge rst)
            begin
                if(!rst)
                begin
                    foreach (dirty_reg[i, j]) dirty_reg[i][j] <= 1'b0;
                end
                else
                begin
                    foreach (dirty_reg[i, j]) dirty_reg[i][j] <= dirty[i][j];
                end
            end

            always_comb
            begin
                next_state = state;
                valid = valid_reg;
                tags = tags_reg;
                data = data_reg;
                MAWAddr_next = MAWAddr;
                MAWLen_next = MAWLen;
                MAWSize_next = MAWSize;
                MAWBurst_next = MAWBurst;
                MAWValid_next = MAWValid;
                MWData_next = MWData;
                MWStrb_next = MWStrb;
                MWLast_next = MWLast;
                MWValid_next = MWValid;
                MBReady_next = MBReady;
                MARAddr_next = MARAddr;
                MARLen_next = MARLen;
                MARSize_next = MARSize;
                MARBurst_next = MARBurst;
                MARValid_next = MARValid;
                counter = counter_reg;
                lru_way_empty = lru_way_empty_reg;
                read_through_hit = read_through_hit_reg;
                refill_error = refill_error_reg;
                dirty = dirty_reg;

                case(state)
                    AXI_IDLE:
                    begin
                        read_through_hit = 1'b0;
                        if(CPUReadEn && !CacheHit)
                        begin
                            if(WAY == 1)
                            begin:direct_mapped_read
                                if (valid_reg[CpuIdx][0] && dirty_reg[CpuIdx][0])
                                begin
                                    next_state = AXI_WR_ADDR;
                                    MAWValid_next = 1'b1;
                                    MAWAddr_next = {tags_reg[CpuIdx][0], CpuIdx, {OFF_W{1'b0}}};
                                    MAWLen_next = LINE_WORDS - 1;
                                    MAWSize_next = TRANSFER_SIZE;
                                    MAWBurst_next = INCR;
                                    counter = '0;
                                    MBReady_next = 1'b0;
                                    refill_error = 1'b0;
                                end
                                else
                                begin
                                    MARValid_next = 1'b1;
                                    MARAddr_next = {CPUAddr[ADDR_WIDTH-1:OFF_W], {OFF_W{1'b0}}};
                                    MARLen_next = LINE_WORDS - 1;
                                    MARSize_next = TRANSFER_SIZE;
                                    MARBurst_next = INCR;
                                    counter = '0;
                                    refill_error = 1'b0;
                                    next_state = AXI_RD_ADDR;

                                end
                            end:direct_mapped_read
                            else
                            begin:set_associative_read
                                if (valid_reg[CpuIdx][victim_way] && dirty_reg[CpuIdx][victim_way])
                                begin
                                    next_state = AXI_WR_ADDR;
                                    MAWValid_next = 1'b1;
                                    MAWAddr_next = (WAY == TOTAL_LINES)
                                                 ? {tags_reg[CpuIdx][victim_way], {OFF_W{1'b0}}}
                                                 : {tags_reg[CpuIdx][victim_way], CpuIdx, {OFF_W{1'b0}}};
                                    MAWLen_next = LINE_WORDS - 1;
                                    MAWSize_next = TRANSFER_SIZE;
                                    MAWBurst_next = INCR;
                                    counter = '0;
                                    MBReady_next = 1'b0;
                                    refill_error = 1'b0;
                                end
                                else
                                begin
                                    MARValid_next = 1'b1;
                                    MARAddr_next = {CPUAddr[ADDR_WIDTH-1:OFF_W], {OFF_W{1'b0}}};
                                    MARLen_next = LINE_WORDS - 1;
                                    MARSize_next = TRANSFER_SIZE;
                                    MARBurst_next = INCR;
                                    counter = '0;
                                    refill_error = 1'b0;
                                    next_state = AXI_RD_ADDR;
                                end
                            end:set_associative_read
                        end
                        else if(CPUWriteEn && CacheHit)
                        begin
                            next_state = AXI_IDLE;
                            MAWValid_next = 1'b0;
                            MARAddr_next = '0;
                            MARValid_next = 1'b0;
                            MARLen_next = '0;
                            MARSize_next = '0;
                            MARBurst_next = INCR;

                            MAWAddr_next = 'b0;
                            MAWLen_next = 'b0;
                            MAWSize_next = 'b0;
                            MAWBurst_next = FIXED;
                            MAWValid_next = 'b0;
                            MWData_next = 'b0;
                            MWStrb_next = 'b0;
                            MWLast_next = 'b0;
                            MWValid_next = 'b0;

                            counter = '0;
                            read_through_hit = 1'b0;
                            if(WAY == 1)
                            begin:direct_mapped_write
                                data[CpuIdx][0][CpuOffset[OFF_W-1:2]] = CPUWriteData;
                                dirty[CpuIdx][0] = 1'b1;
                            end:direct_mapped_write
                            else                            
                            begin:set_associative_write
                                data[CpuIdx][hit_way][CpuOffset[OFF_W-1:2]] = CPUWriteData;
                                dirty[CpuIdx][hit_way] = 1'b1;
                            end:set_associative_write
                        end
                        else if(CPUWriteEn && !CacheHit)
                        begin
                            if(WAY == 1)
                            begin:direct_mapped_write_miss
                                if(valid_reg[CpuIdx][0] && dirty_reg[CpuIdx][0])
                                begin
                                    next_state = AXI_WR_ADDR;
                                    MAWValid_next = 1'b1;
                                    MAWAddr_next = {tags_reg[CpuIdx][0], CpuIdx, {OFF_W{1'b0}}};
                                    MAWLen_next = LINE_WORDS - 1;
                                    MAWSize_next = TRANSFER_SIZE;
                                    MAWBurst_next = INCR;
                                    counter = '0;
                                    MBReady_next = 1'b0;
                                end
                                else
                                begin
                                    MARValid_next = 1'b1;
                                    MARAddr_next = {CPUAddr[ADDR_WIDTH-1:OFF_W], {OFF_W{1'b0}}};
                                    MARLen_next = LINE_WORDS - 1;
                                    MARSize_next = TRANSFER_SIZE;
                                    MARBurst_next = INCR;
                                    counter = '0;
                                    read_through_hit = 1'b0;
                                    refill_error = 1'b0;
                                    next_state = AXI_RD_ADDR;
                                end
                            end:direct_mapped_write_miss
                            else
                            begin:set_associative_write_miss
                                if(valid_reg[CpuIdx][victim_way] && dirty_reg[CpuIdx][victim_way])
                                begin
                                    next_state = AXI_WR_ADDR;
                                    MAWValid_next = 1'b1;
                                    MAWAddr_next = (WAY == TOTAL_LINES)
                                                 ? {tags_reg[CpuIdx][victim_way], {OFF_W{1'b0}}}
                                                 : {tags_reg[CpuIdx][victim_way], CpuIdx, {OFF_W{1'b0}}};
                                    MAWLen_next = LINE_WORDS - 1;
                                    MAWSize_next = TRANSFER_SIZE;
                                    MAWBurst_next = INCR;
                                    counter = '0;
                                    MBReady_next = 1'b0;
                                end
                                else
                                begin
                                    MARValid_next = 1'b1;
                                    MARAddr_next = {CPUAddr[ADDR_WIDTH-1:OFF_W], {OFF_W{1'b0}}};
                                    MARLen_next = LINE_WORDS - 1;
                                    MARSize_next = TRANSFER_SIZE;
                                    MARBurst_next = INCR;
                                    counter = '0;
                                    read_through_hit = 1'b0;
                                    refill_error = 1'b0;
                                    next_state = AXI_RD_ADDR;
                                end
                            end:set_associative_write_miss
                        end
                    end

                    AXI_WR_ADDR:
                    begin
                        if(MAWValid && MAWReady)
                        begin
                            next_state = AXI_WR_DATA;
                            MAWValid_next = 1'b0;
                            MWStrb_next = {(DATA_BYTES){1'b1}};
                            MWValid_next = 1'b1;
                            MWLast_next = (LINE_WORDS == 1);
                            MARAddr_next = '0;
                            MARValid_next = 1'b0;
                            MARLen_next = '0;
                            MARSize_next = '0;
                            MARBurst_next = INCR;
                            if(WAY == 1)
                            begin
                                MWData_next = data_reg[CpuIdx_reg][0][0];
                            end
                            else
                            begin
                                MWData_next = data_reg[CpuIdx_reg][victim_way_reg][0];
                            end
                        end
                    end

                    AXI_WR_DATA:
                    begin
                        if(MWValid && MWReady)
                        begin
                            if(counter_reg == (LINE_WORDS - 1))
                            begin
                                next_state = AXI_WR_RESP;
                                MWValid_next = 1'b0;
                                MWLast_next = 1'b0;
                                MBReady_next = 1'b1;
                                counter = '0;
                            end
                            else
                            begin
                                counter = counter_reg + 1'b1;
                                MWStrb_next = {(DATA_BYTES){1'b1}};
                                MWValid_next = 1'b1;
                                MWLast_next = ((counter_reg + 1'b1) == (LINE_WORDS - 1));
                                if(WAY == 1)
                                begin
                                    MWData_next = data_reg[CpuIdx_reg][0][counter_reg + 1'b1];
                                end
                                else
                                begin
                                    MWData_next = data_reg[CpuIdx_reg][victim_way_reg][counter_reg + 1'b1];
                                end
                            end
                        end
                    end

                    AXI_WR_RESP:
                    begin
                        MBReady_next = 1'b1;
                        if(MBValid && MBReady)
                        begin
                            MBReady_next = 1'b0;
                            MAWValid_next = 1'b0;
                            if(MBResp == AXI_OKAY)
                            begin
                                next_state = AXI_RD_ADDR;
                                MARValid_next = 1'b1;
                                MARAddr_next = miss_line_addr;
                                MARLen_next = LINE_WORDS - 1;
                                MARSize_next = TRANSFER_SIZE;
                                MARBurst_next = INCR;
                                counter = '0;
                                read_through_hit = 1'b0;
                                refill_error = 1'b0;
                                if(WAY == 1)
                                begin
                                    dirty[CpuIdx_reg][0] = 1'b0;
                                end
                                else
                                begin
                                    dirty[CpuIdx_reg][victim_way_reg] = 1'b0;
                                end

                            end
                            else
                            begin
                                next_state = AXI_IDLE;
                                MARValid_next = 1'b0;
                            end
                        end
                    end

                    AXI_RD_ADDR:
                    begin
                        if(MARReady && MARValid)
                        begin
                            next_state = AXI_RD_DATA;
                            MARValid_next = 1'b0;
                            MAWValid_next = 1'b0;
                            MARAddr_next = '0;
                            MARValid_next = 1'b0;
                            MARLen_next = '0;
                            MARSize_next = '0;
                            MARBurst_next = INCR;
                            counter = '0;
                            read_through_hit = 1'b0;
                            refill_error = 1'b0;
                        end
                    end

                    AXI_RD_DATA:
                    begin
                        if(WAY == 1)
                        begin:direct_mapped_read_loop
                            if(MRRValid && MRReady)
                            begin
                                if(MRRResp == AXI_OKAY)
                                begin
                                    data[CpuIdx_reg][0][counter] = MRRData;
                                end
                                else
                                begin
                                    refill_error = 1'b1;
                                end
                                if((MRRResp == AXI_OKAY) && !pending_write &&
                                   (counter == (CpuOffset_reg[OFF_W-1:2])))
                                begin
                                    read_through_hit = 1'b1;
                                end
                                else
                                begin
                                    read_through_hit = 1'b0;
                                end
                                counter = counter + 1'b1;
                                if(MRRLast)
                                begin
                                    if((MRRResp == AXI_OKAY) && !refill_error_reg)
                                    begin
                                        if(pending_write)
                                        begin
                                            data[CpuIdx_reg][0][CpuOffset_reg[OFF_W-1:2]] = pending_write_data;
                                            dirty[CpuIdx_reg][0] = 1'b1;
                                        end
                                        else
                                        begin
                                            dirty[CpuIdx_reg][0] = 1'b0;
                                        end
                                        valid[CpuIdx_reg][0] = 1'b1;
                                        tags[CpuIdx_reg][0] = CpuTag_reg;
                                    end
                                    next_state = AXI_IDLE;
                                    counter = '0;
                                end
                            end
                        end:direct_mapped_read_loop
                        else
                        begin:set_associative_read_loop
                            if(MRRValid && MRReady)
                            begin
                                if(MRRResp == AXI_OKAY)
                                begin
                                    data[CpuIdx_reg][victim_way_reg][counter] = MRRData;
                                end
                                else
                                begin
                                    refill_error = 1'b1;
                                end
                                if((MRRResp == AXI_OKAY) && !pending_write &&
                                   (counter == (CpuOffset_reg[OFF_W-1:2])))
                                begin
                                    read_through_hit = 1'b1;
                                end
                                else
                                begin
                                    read_through_hit = 1'b0;
                                end
                                counter = counter + 1'b1;

                                if(MRRLast)
                                begin
                                    if((MRRResp == AXI_OKAY) && !refill_error_reg)
                                    begin
                                        if(pending_write)
                                        begin
                                            data[CpuIdx_reg][victim_way_reg][CpuOffset_reg[OFF_W-1:2]] = pending_write_data;
                                            dirty[CpuIdx_reg][victim_way_reg] = 1'b1;
                                        end
                                        else
                                        begin
                                            dirty[CpuIdx_reg][victim_way_reg] = 1'b0;
                                        end
                                        valid[CpuIdx_reg][victim_way_reg] = 1'b1;
                                        tags[CpuIdx_reg][victim_way_reg] = CpuTag_reg;
                                    end
                                    next_state = AXI_IDLE;
                                    MAWValid_next = 1'b0;
                                    MARAddr_next = '0;
                                    MARValid_next = 1'b0;
                                    MARLen_next = '0;
                                    MARSize_next = '0;
                                    MARBurst_next = INCR;
                                    counter = '0;
                                    if(!and_valid_bits)
                                    begin
                                        lru_way_empty[CpuIdx_reg] = lru_way_empty[CpuIdx_reg] + 1'b1;
                                    end
                                end
                            end
                        end:set_associative_read_loop
                    end
                endcase
            end
        end:gen_read_write_cache
    endgenerate
endmodule
