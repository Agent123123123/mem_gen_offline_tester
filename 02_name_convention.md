# Step 2: Decoding Foundry Naming Conventions

## Overview

Every foundry assigns encoded names to memory macros. A typical name like `ts5n12ffcllulvta8x16m1swsho` packs **family, process, bitcell, VT, dimensions, segment, and options** into a single string.

Your generator must parse these names bidirectionally:
- **Name → Parameters**: User gives a macro name → extract all parameters
- **Parameters → Name**: User gives (family, width, depth, …) → construct the canonical name

## 2.1 General Name Anatomy

Most foundry names follow this template (details vary):

```
<prefix><process><bitcell><vt><nword>x<nbit>m<nmux><segment><options>
```

### TSMC Convention (Detailed)

```
ts 5 n 12 ff c ll ulvt a 8 x 16 m 1 s  w s h o
│  │ │ │  │  │ │  │    │ │   │  │ │ │  │ │ │ │
│  │ │ │  │  │ │  │    │ └───┴──┘ │ │  │ │ │ └─ Option: SD
│  │ │ │  │  │ │  │    │   NWxNB  │ │  │ │ └─── Option: DualRail
│  │ │ │  │  │ │  │    │         Mux│  │ └───── Option: SLP
│  │ │ │  │  │ │  │    │           Seg │ └─────── Option: BWEB
│  │ │ │  │  │ │  │    │             │ └───────── Options block start
│  │ │ │  │  │ │  │    └─ Suffix: arch variant
│  │ │ │  │  │ │  └────── VT: ulvt/lvt/svt
│  │ │ │  │  │ └───────── Bitcell: LL (Low Leakage)
│  │ │ │  │  └──────────── Compact
│  │ │ │  └─────────────── FinFET
│  │ │ └────────────────── Node: 12nm
│  │ └──────────────────── Identifier
│  └────────────────────── CompNo: 5 = 1PRF, 6 = 2PRF, etc.
└───────────────────────── Foundry prefix: ts = TSMC
```

### Cross-Foundry Comparison

| Component | TSMC | Samsung | SMIC |
|-----------|------|---------|------|
| Prefix | `ts` | `s` or `sec` | `sm` |
| CompNo | Numeric (5/6/…) | Alpha code | Numeric |
| VT encoding | `ulvt/lvt/svt` suffix | `u/l/s/h` inline | `ul/l/s` suffix |
| Dimension format | `NWxNBmMUX` | `NWxNBmMUX` | `NWxNBmMUX` |
| Options | Trailing chars | Flag chars | Similar to TSMC |

**Key insight**: The dimension format `<nw>x<nb>m<mux>` is near-universal across foundries. The prefix, VT, and options encoding varies.

## 2.2 Building a Name Parser

### Strategy: Prefix-Based Family Detection

```python
# Step 1: Try to match prefix → family
PREFIX_MAP = {
    'ts5n12ffcll': '1prf',
    'ts6n12ffcll': '2prf',
    'tsn12ffcll':  None,    # ambiguous, needs further check
}

# Step 2: For ambiguous prefixes, use secondary patterns
# e.g., 'tsn12ffcll' + check if suffix matches spsram/dpsram/uhd patterns
```

### Core Regex: Extract Dimensions

The dimension block is the most stable part across all foundries:

```python
import re

DIM_REGEX = re.compile(
    r'(?:^|[a-z])(\d+)x(\d+)m(\d+)([smf]?)',
    re.IGNORECASE
)

match = DIM_REGEX.search(memory_name)
if match:
    nword   = int(match.group(1))
    nbit    = int(match.group(2))
    nmux    = int(match.group(3))
    segment = match.group(4) or ''
```

### VT Detection

```python
VT_PATTERNS = ['ulvt', 'lvt', 'svt', 'hvt']

def detect_vt(name: str) -> str:
    name_lower = name.lower()
    # Must check longer patterns first (ulvt before lvt)
    for vt in sorted(VT_PATTERNS, key=len, reverse=True):
        if vt in name_lower:
            return vt
    return 'default'  # some families don't encode VT in name
```

### Option Token Extraction

After consuming the dimension block, remaining characters encode feature flags:

```python
OPTION_TOKENS = {
    'w': 'BWEB',      # Byte Write Enable
    'b': 'BIST',      # Built-in Self Test (typically disabled)
    'a': 'SLP',       # Sleep mode
    's': 'SD',        # Shut Down
    'o': 'DSLP',      # Deep Sleep
    'd': 'DualRail',  # Dual power rail
    'c': 'ColRed',    # Column Redundancy
    'h': 'DualRail',  # Alternative encoding
    'p': 'Power',     # Power-down
    'r': 'Repair',    # Redundancy repair
}

def parse_options(option_string: str, family_spec) -> list[str]:
    options = []
    for char in option_string:
        if char in OPTION_TOKENS:
            options.append(char)
        else:
            raise ParseError(f"Unknown option token: '{char}'")
    return options
```

## 2.3 Name → Config Line Translation

Once parsed, convert to compiler-specific config format:

```python
def build_config_line(spec: MemorySpec, family: FamilySpec) -> str:
    """Convert parsed spec → config.txt content."""
    base = f"{spec.words}x{spec.bits}m{spec.mux}{spec.segment}"
    
    # Add option flags as compiler flags
    flags = []
    for opt in spec.options:
        if opt in family.default_tokens and opt not in spec.active_options:
            flags.append(family.negative_flag_map[opt])  # e.g., -NonBWEB
        elif opt not in family.default_tokens and opt in spec.active_options:
            flags.append(family.positive_flag_map[opt])  # e.g., -DualRail
    
    return base + " " + " ".join(flags)
```

### The "Default vs Explicit" Flag Pattern

This is a critical design concept:

```
TSMC pattern:
  • Default-ON features → present in name → must add -NonXXX to disable
  • Default-OFF features → present in name → must add -XXX to enable
  • Feature in name + in defaults → it's already on, no flag needed
  • Feature in name + NOT in defaults → must emit positive flag

Config flags are DIFFERENTIAL: only emit what differs from defaults.
```

## 2.4 Validation Rules

Each family has dimension constraints. Building a validator:

```python
def validate_spec(spec: MemorySpec, family: FamilySpec) -> None:
    """Raise if spec violates family constraints."""
    
    # MUX-dependent word depth range
    ranges = VALID_RANGES[(family.family_id, spec.mux)]
    if spec.words < ranges.nw_min or spec.words > ranges.nw_max:
        raise WrapperError(f"NWORD {spec.words} out of range for MUX={spec.mux}")
    
    if spec.words % ranges.nw_step != 0:
        raise WrapperError(f"NWORD must be multiple of {ranges.nw_step}")
    
    # NBIT range
    if spec.bits < ranges.nb_min or spec.bits > ranges.nb_max:
        raise WrapperError(f"NBIT {spec.bits} out of range")
    
    # Segment validity
    if spec.segment and spec.segment not in family.allowed_segments:
        raise WrapperError(f"Segment '{spec.segment}' not allowed")
    
    # Forbidden tokens
    for token in FORBIDDEN_TOKENS:  # e.g., 'b' for BIST
        if token in spec.options:
            raise WrapperError(f"Token '{token}' is forbidden")
```

## 2.5 Foundry-Agnostic Abstraction Layers

To support multiple foundries without rewriting parsing logic:

```python
# Layer 1: Generic MemorySpec (foundry-independent)
@dataclass
class MemorySpec:
    raw_name: str
    canonical_name: str
    family: str
    words: int
    bits: int
    mux: int
    vt: str
    segment: str
    options: list[str]

# Layer 2: FamilySpec (per-family, per-foundry config)
@dataclass
class FamilySpec:
    family_id: str
    compiler_dir: Path
    positive_flag_map: dict[str, str]
    negative_flag_map: dict[str, str]
    ...

# Layer 3: Foundry adapter (translates between generic + foundry-specific)
class FoundryAdapter:
    def parse_name(self, name: str) -> MemorySpec: ...
    def build_config(self, spec: MemorySpec) -> str: ...
    def invoke_compiler(self, config_path: Path) -> CompletedProcess: ...
```

This three-layer approach means adding a new foundry (e.g., Samsung 5nm) only requires implementing a new adapter — the wrapper design, tiling engine, and CLI layer remain unchanged.
