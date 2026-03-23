"""
name_parser_skeleton.py — Foundry-agnostic memory name parser reference.

This is a SKELETON — fill in the regex patterns and family maps
for your specific foundry/node. The architecture is portable.

Usage:
    spec = parse_memory_name("ts5n12ffcllulvta8x16m1swsho")
    print(spec.family, spec.words, spec.bits, spec.vt)
"""
from __future__ import annotations

import re
from dataclasses import dataclass


# ── Data model (foundry-agnostic) ────────────────────────────────

@dataclass
class MemorySpec:
    """Normalised view of a single memory macro, regardless of foundry."""
    raw_name:   str
    family:     str        # e.g. "spsram", "1prf", "dpsram"
    words:      int        # depth  (number of addresses)
    bits:       int        # width  (data bits per word)
    mux:        int | None # mux ratio, if encoded in name
    vt:         str        # voltage threshold: "ulvt", "lvt", "svt", "hvt"
    options:    dict       # extra flags parsed from the name


# ── Foundry adapter interface ────────────────────────────────────

class FoundryAdapter:
    """
    Override these methods for each foundry/node.
    The parse() method calls them in order.
    """

    # --- Step 1: Family detection ---
    # Map from name-prefix to canonical family name.
    # Order matters: most-specific prefixes first.
    FAMILY_PREFIX_MAP: list[tuple[str, str]] = [
        # ("prefix_in_name", "canonical_family"),
        # e.g. ("ts5n", "1prf"), ("ts6n", "2prf"), ...
    ]

    # --- Step 2: Dimension extraction ---
    # Regex applied to the name to pull out words × bits.
    # Must have named groups 'words' and 'bits'.
    DIMENSION_REGEX: str = r"(?P<words>\d+)x(?P<bits>\d+)"

    # --- Step 3: Mux extraction ---
    MUX_REGEX: str = r"m(?P<mux>\d+)"

    # --- Step 4: VT detection ---
    # Longest first to avoid "lvt" matching inside "ulvt".
    VT_OPTIONS: list[str] = ["ulvt", "lvt", "svt", "hvt"]

    # --- Step 5: Option tokens ---
    # Foundry-specific tokens at the end of the name.
    OPTION_TOKENS: dict[str, tuple[str, ...]] = {
        # "option_name": ("token_if_true", "token_if_false"),
        # e.g. "write_thru": ("wt", "nwt"),
    }

    def detect_family(self, name: str) -> str:
        """Return canonical family string, or raise ValueError."""
        lower = name.lower()
        for prefix, family in self.FAMILY_PREFIX_MAP:
            if prefix in lower:
                return family
        raise ValueError(f"Unknown family in name: {name}")

    def extract_dimensions(self, name: str) -> tuple[int, int]:
        """Return (words, bits)."""
        m = re.search(self.DIMENSION_REGEX, name)
        if not m:
            raise ValueError(f"Cannot extract dimensions from: {name}")
        return int(m.group("words")), int(m.group("bits"))

    def extract_mux(self, name: str) -> int | None:
        """Return mux ratio if present, else None."""
        m = re.search(self.MUX_REGEX, name)
        return int(m.group("mux")) if m else None

    def detect_vt(self, name: str) -> str:
        """Return VT string (longest match first)."""
        lower = name.lower()
        for vt in self.VT_OPTIONS:
            if vt in lower:
                return vt
        return "svt"  # default

    def extract_options(self, name: str) -> dict:
        """Return dict of boolean/string options parsed from the name."""
        lower = name.lower()
        opts = {}
        for key, tokens in self.OPTION_TOKENS.items():
            for tok in tokens:
                if tok in lower:
                    opts[key] = tok
                    break
        return opts

    def parse(self, name: str) -> MemorySpec:
        """Full parse pipeline."""
        family = self.detect_family(name)
        words, bits = self.extract_dimensions(name)
        mux = self.extract_mux(name)
        vt = self.detect_vt(name)
        options = self.extract_options(name)
        return MemorySpec(
            raw_name=name,
            family=family,
            words=words,
            bits=bits,
            mux=mux,
            vt=vt,
            options=options,
        )


# ── Example: TSMC 12nm adapter (fill in for your foundry) ───────

class TSMC12nmAdapter(FoundryAdapter):
    """Concrete adapter for TSMC 12FFC memory compilers."""

    FAMILY_PREFIX_MAP = [
        ("ts5n",           "1prf"),
        ("ts6n",           "2prf"),
        ("tsdn",           "dpsram"),
        ("ts1n",           "spsram"),
        # Add more families...
    ]

    DIMENSION_REGEX = r"a(?P<words>\d+)x(?P<bits>\d+)"
    MUX_REGEX = r"m(?P<mux>\d+)"
    VT_OPTIONS = ["ulvt", "lvt", "svt", "hvt"]

    OPTION_TOKENS = {
        "write_thru":  ("wt",),
        "shutdown":    ("sho", "shno"),
        "fast_wakeup": ("fw",),
    }


# ── Main ─────────────────────────────────────────────────────────

def parse_memory_name(name: str, adapter: FoundryAdapter | None = None) -> MemorySpec:
    """
    Parse a foundry memory name into a MemorySpec.

    Args:
        name:    Raw compiler-convention memory name.
        adapter: Foundry-specific adapter. Defaults to TSMC12nmAdapter.

    Returns:
        MemorySpec with parsed fields.
    """
    if adapter is None:
        adapter = TSMC12nmAdapter()
    return adapter.parse(name)


if __name__ == "__main__":
    # Quick test
    test_names = [
        "ts5n12ffcllulvta8x16m1swsho",
        "ts6n12ffcllulvta16x8m1fwsho",
        "ts1n12ffcllulvta256x32m4swbasodcp",
    ]
    adapter = TSMC12nmAdapter()
    for n in test_names:
        try:
            spec = adapter.parse(n)
            print(f"  {n}")
            print(f"    family={spec.family}  {spec.words}x{spec.bits}"
                  f"  mux={spec.mux}  vt={spec.vt}  opts={spec.options}")
        except ValueError as e:
            print(f"  FAIL: {e}")
