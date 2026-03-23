"""
tiling_engine_skeleton.py — Foundry-agnostic tiling calculator reference.

Pure math: no file I/O, no compiler invocation, no framework dependency.
Given (child_words, child_bits, exposed_width, exposed_depth), compute
the tile grid and per-tile address/data mapping.

Usage:
    mapping = compute_tiling(
        child_words=8, child_bits=16,
        exposed_width=40, exposed_depth=20,
    )
    print(mapping)
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field


# ── Data model ───────────────────────────────────────────────────

@dataclass
class TileInfo:
    """Describes one tile's position and mapping in the final wrapper."""
    col:            int       # horizontal tile index (0-based)
    row:            int       # vertical tile index   (0-based)
    instance_name:  str       # suggested instance name, e.g. "tile_r0_c1"
    bit_lo:         int       # inclusive low bit in exposed data bus
    bit_hi:         int       # exclusive high bit in exposed data bus
    addr_lo:        int       # inclusive low address in exposed address space
    addr_hi:        int       # exclusive high address in exposed address space
    is_edge_h:      bool      # True if this is the last column (may need bit padding)
    is_edge_v:      bool      # True if this is the last row (may have fewer valid addrs)
    pad_bits:       int       # number of unused upper bits in this tile's data port
    valid_words:    int       # number of valid addresses in this tile


@dataclass
class TileMapping:
    """Complete tiling plan for one memory wrapper."""
    child_words:    int
    child_bits:     int
    exposed_width:  int
    exposed_depth:  int
    cols:           int       # number of horizontal tiles
    rows:           int       # number of vertical tiles
    total_tiles:    int
    tiles:          list[TileInfo] = field(default_factory=list)

    @property
    def total_child_bits(self) -> int:
        return self.cols * self.child_bits

    @property
    def total_child_words(self) -> int:
        return self.rows * self.child_words

    @property
    def waste_bits(self) -> int:
        return self.total_child_bits - self.exposed_width

    @property
    def waste_words(self) -> int:
        return self.total_child_words - self.exposed_depth


# ── Core algorithm ───────────────────────────────────────────────

def ceil_div(a: int, b: int) -> int:
    """Integer ceiling division."""
    return (a + b - 1) // b


def compute_tiling(
    child_words: int,
    child_bits: int,
    exposed_width: int,
    exposed_depth: int,
) -> TileMapping:
    """
    Compute the tiling plan for a memory wrapper.

    Args:
        child_words:   Depth (addresses) of the base macro.
        child_bits:    Width (data bits) of the base macro.
        exposed_width: Desired total data width of the wrapper.
        exposed_depth: Desired total depth of the wrapper.

    Returns:
        TileMapping with per-tile info.

    Raises:
        ValueError: if inputs are non-positive.
    """
    if any(v <= 0 for v in (child_words, child_bits, exposed_width, exposed_depth)):
        raise ValueError("All dimensions must be positive integers")

    cols = ceil_div(exposed_width, child_bits)
    rows = ceil_div(exposed_depth, child_words)

    tiles = []
    for r in range(rows):
        for c in range(cols):
            bit_lo = c * child_bits
            bit_hi = min((c + 1) * child_bits, exposed_width)
            addr_lo = r * child_words
            addr_hi = min((r + 1) * child_words, exposed_depth)

            is_edge_h = (c == cols - 1) and (exposed_width % child_bits != 0)
            is_edge_v = (r == rows - 1) and (exposed_depth % child_words != 0)

            used_bits = bit_hi - bit_lo
            pad_bits = child_bits - used_bits
            valid_words = addr_hi - addr_lo

            tiles.append(TileInfo(
                col=c,
                row=r,
                instance_name=f"tile_r{r}_c{c}",
                bit_lo=bit_lo,
                bit_hi=bit_hi,
                addr_lo=addr_lo,
                addr_hi=addr_hi,
                is_edge_h=is_edge_h,
                is_edge_v=is_edge_v,
                pad_bits=pad_bits,
                valid_words=valid_words,
            ))

    return TileMapping(
        child_words=child_words,
        child_bits=child_bits,
        exposed_width=exposed_width,
        exposed_depth=exposed_depth,
        cols=cols,
        rows=rows,
        total_tiles=cols * rows,
        tiles=tiles,
    )


# ── Pretty printer ───────────────────────────────────────────────

def print_tiling(m: TileMapping) -> None:
    """Print a human-readable tiling summary."""
    print(f"Tiling Plan: {m.exposed_width}b × {m.exposed_depth}w "
          f"using {m.child_bits}b × {m.child_words}w macros")
    print(f"  Grid: {m.cols} cols × {m.rows} rows = {m.total_tiles} tiles")
    print(f"  Waste: {m.waste_bits} bits, {m.waste_words} words")
    print()

    # Visual grid
    print("  Tile Grid (col → bit range, row → addr range):")
    for r in range(m.rows):
        row_tiles = [t for t in m.tiles if t.row == r]
        parts = []
        for t in sorted(row_tiles, key=lambda x: x.col):
            label = f"[{t.bit_lo}:{t.bit_hi})"
            if t.pad_bits > 0:
                label += f"(+{t.pad_bits}pad)"
            parts.append(label)
        addr_range = f"addr[{row_tiles[0].addr_lo}:{row_tiles[0].addr_hi})"
        valid = row_tiles[0].valid_words
        suffix = f"  ({valid}/{m.child_words} valid)" if valid < m.child_words else ""
        print(f"    row {r} {addr_range}{suffix}: {' | '.join(parts)}")
    print()

    # Per-tile detail
    for t in m.tiles:
        flags = []
        if t.is_edge_h:
            flags.append("edge-H")
        if t.is_edge_v:
            flags.append("edge-V")
        flag_str = f"  [{', '.join(flags)}]" if flags else ""
        print(f"  {t.instance_name}: "
              f"bits[{t.bit_lo}:{t.bit_hi}) addr[{t.addr_lo}:{t.addr_hi}) "
              f"pad={t.pad_bits}{flag_str}")


# ── JSON export ──────────────────────────────────────────────────

def to_dict(m: TileMapping) -> dict:
    """Convert TileMapping to a JSON-serializable dict."""
    return {
        "child_words": m.child_words,
        "child_bits": m.child_bits,
        "exposed_width": m.exposed_width,
        "exposed_depth": m.exposed_depth,
        "cols": m.cols,
        "rows": m.rows,
        "total_tiles": m.total_tiles,
        "waste_bits": m.waste_bits,
        "waste_words": m.waste_words,
        "tiles": [
            {
                "instance_name": t.instance_name,
                "col": t.col,
                "row": t.row,
                "bit_lo": t.bit_lo,
                "bit_hi": t.bit_hi,
                "addr_lo": t.addr_lo,
                "addr_hi": t.addr_hi,
                "is_edge_h": t.is_edge_h,
                "is_edge_v": t.is_edge_v,
                "pad_bits": t.pad_bits,
                "valid_words": t.valid_words,
            }
            for t in m.tiles
        ],
    }


# ── Self-test ────────────────────────────────────────────────────

if __name__ == "__main__":
    import json

    print("=" * 60)
    print("Example 1: 40b × 20w using 16b × 8w macros")
    print("=" * 60)
    m1 = compute_tiling(child_words=8, child_bits=16,
                        exposed_width=40, exposed_depth=20)
    print_tiling(m1)

    print("=" * 60)
    print("Example 2: 128b × 512w using 32b × 256w macros (exact fit)")
    print("=" * 60)
    m2 = compute_tiling(child_words=256, child_bits=32,
                        exposed_width=128, exposed_depth=512)
    print_tiling(m2)

    print("=" * 60)
    print("Example 3: 33b × 100w using 16b × 64w macros (edge cases)")
    print("=" * 60)
    m3 = compute_tiling(child_words=64, child_bits=16,
                        exposed_width=33, exposed_depth=100)
    print_tiling(m3)

    # JSON export demo
    print("\nJSON export (Example 1):")
    print(json.dumps(to_dict(m1), indent=2))
