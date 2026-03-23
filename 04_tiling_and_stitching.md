# Step 4: Tiling & RTL Stitching

## Overview

A single memory macro has a fixed (NWORD × NBIT) size — e.g., 64 words × 16 bits. When a design needs a memory with different dimensions (say 256 words × 128 bits), multiple macros must be tiled together and wrapped in RTL that presents a single unified interface.

This document describes the **tiling algorithm** and **RTL wrapper generation** strategy.

## 4.1 The Tiling Problem

```
User wants:  width=128, depth=256
Macro gives: width=16,  depth=64

Solution:    128/16 = 8  columns  (horizontal expansion — data width)
             256/64 = 4  rows     (vertical expansion — address depth)
             Total:      32 macro instances
```

### Two Dimensions of Expansion

| Dimension | Direction | What it expands | Tile count formula | RTL mechanism |
|-----------|-----------|----------------|-------------------|---------------|
| Horizontal | Width | Data bus | `ceil(exposed_width / child_bits)` | Bus concatenation |
| Vertical | Depth | Address space | `ceil(exposed_depth / child_words)` | Address decode + mux |

## 4.2 Core Calculation

```python
def ceil_div(a: int, b: int) -> int:
    return (a + b - 1) // b

def compute_tiling(exposed_width, exposed_depth, child_bits, child_words):
    h_tiles = ceil_div(exposed_width, child_bits)    # columns
    v_tiles = ceil_div(exposed_depth, child_words)    # rows
    
    padded_width = h_tiles * child_bits    # may exceed exposed_width
    padded_depth = v_tiles * child_words   # may exceed exposed_depth
    
    return h_tiles, v_tiles, padded_width, padded_depth
```

### Edge Padding

When dimensions don't divide evenly:

```
Example: exposed_width=40, child_bits=16
  h_tiles = ceil(40/16) = 3
  padded_width = 48
  
  Column 0: bits [15:0]    → 16 valid bits
  Column 1: bits [31:16]   → 16 valid bits
  Column 2: bits [39:32]   → 8 valid bits + 8 padding bits
```

**Write path**: Pad unused data bits with 0 (or 1 for BWEB)
**Read path**: Truncate padded bits — top wrapper only exposes [exposed_width-1:0]

## 4.3 TileMapping Data Structure

Each macro instance gets a mapping that tells the RTL generator exactly what it's responsible for:

```python
@dataclass(frozen=True)
class TileMapping:
    row: int                  # Vertical position (0 = first row)
    col: int                  # Horizontal position (0 = first column)
    instance_name: str        # e.g., "u_tile_r0_c0"
    
    # Data mapping
    data_bit_low: int         # First data bit this tile handles
    data_bit_high: int        # Last data bit (may be < low + child_bits - 1)
    valid_data_bits: int      # How many bits are real (not padding)
    padded_data_bits: int     # How many bits are padding
    
    # Address mapping
    depth_start: int          # First address in this tile's range
    depth_end: int            # Last address (macro boundary)
    valid_depth_end: int      # Last valid address (may be < depth_end)
```

### Generating Tile Mappings

```python
tiles = []
for row in range(v_tiles):
    depth_start = row * child_words
    depth_end   = depth_start + child_words - 1
    valid_depth = min(depth_end, exposed_depth - 1)
    
    for col in range(h_tiles):
        bit_low  = col * child_bits
        bit_high = min(exposed_width, bit_low + child_bits) - 1
        valid_bits = bit_high - bit_low + 1
        pad_bits   = child_bits - valid_bits
        
        tiles.append(TileMapping(
            row=row, col=col,
            instance_name=f"u_tile_r{row}_c{col}",
            data_bit_low=bit_low,
            data_bit_high=bit_high,
            valid_data_bits=valid_bits,
            padded_data_bits=pad_bits,
            depth_start=depth_start,
            depth_end=depth_end,
            valid_depth_end=valid_depth,
        ))
```

## 4.4 Two-Layer Wrapper Architecture

### Layer 1: Tile Wrapper

```
                    ┌──────────────────────────────┐
  CLK ─────────────▶│                              │
  CEN ─────────────▶│      Tile Wrapper            │
  WEN ─────────────▶│  (single macro + padding)    │
  A[addr_w-1:0] ───▶│                              │
  D[child_bits-1:0]▶│    ┌──────────────┐          │
  BWEN[...]────────▶│    │  MC Macro    │          │
                    │    │  (VComponent) │         │
  Q[child_bits-1:0]◀│    │              │          │
                    │    └──────────────┘          │
                    └──────────────────────────────┘
```

Tile wrapper responsibilities:
- Import the compiler-generated macro via `VComponent` (UHDL) or `module` instantiation
- Handle bit-width padding on D/BWEN when this tile is on the edge column
- Present a clean, uniform port interface upward

### Layer 2: Top Stitching Wrapper

```
                    ┌───────────────────────────────────┐
  CLK ─────────────▶│           Top Wrapper             │
  CEN ─────────────▶│                                   │
  WEN ─────────────▶│  ┌───────┐ ┌───────┐ ┌───────┐   │
  A[addr_w-1:0]───▶│  │ tile  │ │ tile  │ │ tile  │   │
  D[exp_w-1:0]────▶│  │ r0c0  │ │ r0c1  │ │ r0c2  │   │
  BWEN[...]───────▶│  └───────┘ └───────┘ └───────┘   │
                    │  ┌───────┐ ┌───────┐ ┌───────┐   │
  Q[exp_w-1:0]◀────│  │ tile  │ │ tile  │ │ tile  │   │
                    │  │ r1c0  │ │ r1c1  │ │ r1c2  │   │
                    │  └───────┘ └───────┘ └───────┘   │
                    └───────────────────────────────────┘
```

Top wrapper responsibilities:
- **Address decode**: Convert top-level address → (row_select, local_address)
- **Row selection**: Enable only the target row's tile (drive CEN/WEN per row)
- **Data concatenation**: Stitch all columns' Q outputs → top Q bus
- **Data distribution**: Split top D/BWEN bus → per-column tile inputs

## 4.5 Address Decode Logic

### Single Row (v_tiles == 1)

No decode needed — address passes through directly.

### Multiple Rows

```python
# Priority chain approach
def build_row_decode(addr, plan):
    if plan.vertical_tiles == 1:
        return const(0), addr[child_addr_bits-1:0]
    
    # Chained when/then/otherwise
    row_sel = EmptyWhen()
    local_addr = EmptyWhen()
    
    for row in range(plan.vertical_tiles - 1):
        boundary = (row + 1) * plan.child_words
        cond = addr < boundary
        row_sel = row_sel.when(cond).then(const(row))
        local_addr = local_addr.when(cond).then(
            (addr - const(row * plan.child_words))[child_addr_bits-1:0]
        )
    
    # Last row: default
    last_row = plan.vertical_tiles - 1
    row_sel.otherwise(const(last_row))
    local_addr.otherwise(
        (addr - const(last_row * plan.child_words))[child_addr_bits-1:0]
    )
    
    return row_sel, local_addr
```

**Why priority chain, not binary decode?**
- Works correctly even when `exposed_depth` is not a power of 2
- Handles partial last row naturally (addresses beyond exposed_depth route to last row but are never actually used)
- Synthesizes to a small comparator chain — area overhead is negligible vs. the memory macros

### Read Data Mux

```python
# Row-select the read output
q_mux = EmptyWhen()
for row in range(plan.vertical_tiles - 1):
    q_mux = q_mux.when(row_sel_delayed == const(row)).then(q_row[row])
q_mux.otherwise(q_row[plan.vertical_tiles - 1])
```

**Critical**: Row select for the read mux must use a **1-cycle delayed** row_sel (register the selection at clock edge), because memory read data appears 1 cycle after address is applied.

## 4.6 Interface Type Handling

Different memory families have different port sets:

| Interface | Ports | Read mechanism |
|-----------|-------|---------------|
| Single Port | CLK, CEN, WEN, A, D, Q, BWEN | WEN=1→read, WEN=0→write |
| One-Read-One-Write | CLK, A_rd, A_wr, D, Q, WEN | Separate R/W address |
| Dual Port | CLK_A, CLK_B, CEN_A, CEN_B, A_A, A_B, D_A, D_B, Q_A, Q_B | Two independent ports |

The tiling/stitching logic is structurally identical across interface types — only the port list changes. Use a dispatch table:

```python
def build_wrapper(plan):
    if plan.interface_class == 'single_port':
        return build_single_port_wrapper(plan)
    elif plan.interface_class == 'one_read_one_write':
        return build_1r1w_wrapper(plan)
    elif plan.interface_class == 'dual_port':
        return build_dual_port_wrapper(plan)
```

## 4.7 RTL Generation Framework Choice

### Option A: String Templates (Simple but Fragile)

```python
verilog = f"""
module {top_name} (
    input clk,
    input [{addr_w-1}:0] addr,
    ...
);
"""
```

**Pros**: Zero dependencies, easy to understand
**Cons**: Bug-prone for complex tiling, no structural validation, hard to maintain

### Option B: Python HDL Framework (Recommended)

Use a Python HDL like UHDL, PyRTL, or Amaranth:

```python
class TopWrapper(Component):
    def __init__(self):
        super().__init__()
        self.clk = Input(UInt(1))
        self.addr = Input(UInt(addr_bits))
        self.q = Output(UInt(exposed_width))
        
        # Instantiate tiles
        for row in range(v_tiles):
            for col in range(h_tiles):
                tile = VComponent(macro_verilog_path)
                setattr(self, f'u_r{row}_c{col}', tile)
```

**Pros**: Structural correctness guaranteed, automatic width inference, reusable
**Cons**: Additional dependency (UHDL or similar framework)

### Recommendation

Use Python HDL for the wrapper generator. String-template RTL is acceptable for simple wrappers (single tile, no stitching), but becomes unmanageable for multi-row, multi-column arrays with padding.

## 4.8 Output Artifacts

```
wrapper_rtl/
├── <name>_tile_wrapper.v              # Layer 1: single-tile wrapper
├── <name>_top_wrapper.v               # Layer 2: stitched top wrapper
├── <name>_top_wrapper.mapping.json    # Tile-to-address/data mapping
└── filelist.f                         # 3-line filelist for simulation
```

### filelist.f Contents

```
/path/to/macro_model.v      ← compiler-generated behavioral model
/path/to/tile_wrapper.v     ← generated Layer 1
/path/to/top_wrapper.v      ← generated Layer 2
```

Users can include this filelist directly in VCS/Xrun: `-f filelist.f`

### mapping.json Contents

```json
{
  "family_id": "1prf",
  "interface_class": "single_port",
  "exposed_width": 40,
  "exposed_depth": 20,
  "horizontal_tiles": 3,
  "vertical_tiles": 3,
  "tiles": [
    {"row": 0, "col": 0, "data_bit_low": 0, "data_bit_high": 15, ...},
    {"row": 0, "col": 1, "data_bit_low": 16, "data_bit_high": 31, ...},
    ...
  ]
}
```
