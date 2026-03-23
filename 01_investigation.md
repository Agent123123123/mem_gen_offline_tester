# Step 1: Investigating a New Memory Compiler Installation

## Overview

Before writing any code, you must **map the terrain** — discover what memory compilers exist, how they're invoked, what their inputs/outputs look like, and what constraints they enforce.

This step is foundry-agnostic. The same investigation process works for TSMC, Samsung, Intel, SMIC, or any foundry that provides batch-mode memory compilers.

## 1.1 Locate the Compiler Installation

Foundry memory compilers are typically installed under a shared EDA path:

```
/data/foundry/<FOUNDRY>/<NODE>/Memory_compiler/
  └── <comp_no>/
      └── <version>/
          ├── <compiler_script>     # Perl, Python, or shell entry point
          ├── config_example.txt    # Sample config (if provided)
          └── doc/
              └── databook.pdf      # Compiler databook
```

### What to Record

| Item | Example | Where to Find |
|------|---------|---------------|
| Root path | `/data/foundry/TSMC12/Memory_compiler/` | Ask PDK admin or `module avail` |
| Compiler families | spsram, dpsram, 1prf, 2prf, rom… | `ls` the root dir, or read docs |
| Entry script | `tsn12ffcllspsram_130b.pl` | Inside each family dir |
| EDA environment | `module load mc2_n12/2013.12` | Ask IT, or check PDK install notes |
| License type | Evaluation / Formal | Databook chapter 5 |

### Checklist

- [ ] Can you `ls` the compiler root directory?
- [ ] Can you `module load` the EDA environment?
- [ ] Does the license server respond? (check `lmstat`)
- [ ] Can you run one compiler in batch mode and get output?

## 1.2 Enumerate Compiler Families

Each "family" is a distinct memory type with its own compiler script and configuration space.

**Strategy**: List subdirectories under the MC root. Each typically maps to one family.

```bash
# Example for TSMC 12nm
ls /data/foundry/TSMC12/Memory_compiler/
#   TS5N12FFCLL/        → 1prf
#   TS6N12FFCLL/        → 2prf
#   TSN12FFCLL_SPSRAM/  → spsram
#   TSN12FFCLL_DPSRAM/  → dpsram
#   ...
```

### For Each Family, Collect

```python
# Template for your internal registry
FAMILY = {
    'family_id':         'spsram',           # your normalized name
    'description':       'Single Port SRAM',
    'compiler_dir':      Path('/data/foundry/.../TSN12FFCLL_SPSRAM/130b/'),
    'script_name':       'tsn12ffcllspsram_130b.pl',
    'comp_no':           'TSN12FFCLLSPSRAM',
    'bitcell':           'LL',
    'compiler_version':  '130b',
    'has_segment':       True,               # does config use s/m/f suffix?
}
```

## 1.3 Understand Config File Format

Every foundry compiler takes a config file (usually `.txt`) that describes the memory instance. **The format varies between families and sometimes between foundries.**

### General Pattern

```
<nword>x<nbit>m<nmux><segment><option_flags>\t<vt>
```

### Discovery Method

1. Check if the compiler directory has `config_example.txt` or sample configs
2. Read the databook "Compiler Installation and Execution" chapter
3. Run the compiler with `--help` or `-h`
4. If none of the above, try a minimal config and read error messages

### Key Variables to Identify

| Variable | Meaning | Examples |
|----------|---------|---------|
| NWORD | Word depth | 32, 64, 128, 512, 2048 |
| NBIT | Data width (bits) | 8, 16, 32, 64, 128 |
| NMUX | MUX ratio | 1, 2, 4, 8, 16 |
| Segment | Physical layout variant | s (single), m (multi), f (fast) |
| VT | Voltage threshold | ulvt, lvt, svt, hvt |
| Options | Feature flags | w(BWEB), b(BIST), a(SLP), s(SD), d(DualRail)… |

### Cross-Foundry Differences

| Foundry | Config format | VT position | Segment |
|---------|--------------|-------------|---------|
| TSMC | `NWxNBmMUXseg_opts\tVT` | Tab-separated suffix | s/m/f |
| Samsung | Varies by node | Inline flag | Often absent |
| Intel | JSON-based on newer nodes | In JSON field | Bank-count |
| SMIC | Similar to TSMC | Inline | s/m |

**Key principle**: Never hardcode one foundry's format. Build a per-family `config_fmt` template.

## 1.4 Discover Valid Ranges

For each (family, nmux, vt), the compiler accepts only specific (NWORD, NBIT) combinations.

### Quick Discovery (Dry Run)

Many compilers have a validation/dry-run mode:

```bash
# TSMC style: run with -DATASHEET only (cheapest output)
perl compiler.pl -file config.txt -DATASHEET
# If NW/NB is outside valid range, you get an error message immediately
```

### Systematic Enumeration

```python
# Scan strategy:
# 1. NWORD range depends on NMUX
NW_SCAN = {
    1:  range(4,    1200,   4),
    2:  range(8,    2400,   8),
    4:  range(16,   4800,  16),
    8:  range(32,   9600,  32),
    16: range(64,  19200,  64),
}

# 2. NBIT typically 1..300 (check compiler errors for actual max)
NB_SCAN = range(1, 300)

# 3. For each (NW, NB): write config, run compiler in validate mode
#    Record which combos succeed → valid_configs[(family, nmux, vt)]
```

### What to Watch For

- **NWORD is NOT a simple range** — some values are excluded by the compiler's internal sub-array constraints. You MUST enumerate, not assume.
- **NBIT max depends on NMUX** — higher MUX often supports narrower max NBIT
- **Some families have very restricted spaces** — e.g., UHD types may only support nmux=1, vt=ulvt

## 1.5 Identify Output Kit Structure

Run the compiler once successfully and inspect outputs:

```bash
# Typical output tree:
<cell_name>_<version>/
├── VERILOG/
│   └── <cell_name>_<version>.v         # Behavioral model
├── DATASHEET/
│   └── <cell_name>_<version>_*.ds      # Per-PVT datasheet
├── NLDM/
│   └── <cell_name>_<version>_*.lib     # Liberty timing
├── LEF/
│   └── <cell_name>_<version>.lef       # Physical abstract
├── SPICE/
│   └── <cell_name>_<version>.spi       # SPICE netlist
└── GDSII/
    └── <cell_name>_<version>.gds       # Layout data
```

**Record**: Which kits does each family support? Not all families generate all kits.

## 1.6 Discover Option Flags

Most compilers support optional feature flags. These fall into two categories:

### Default-ON Options (must be explicitly disabled)

| Flag | Feature | Disable syntax |
|------|---------|---------------|
| BWEB | Byte Write Enable | `-NonBWEB` |
| SLP | Sleep mode | `-NonSLP` |
| DSLP | Deep Sleep | `-NonDSLP` |
| SD | Shut Down | `-NonSD` |

### Default-OFF Options (must be explicitly enabled)

| Flag | Feature | Enable syntax |
|------|---------|--------------|
| DualRail | Dual power rail | `-DualRail` |
| ColRed | Column redundancy | `-ColRed` |

### How to Discover

1. Databook "Compiler Options" chapter (most reliable)
2. The naming convention table in the databook
3. Run compiler with wrong flags → read error messages
4. Inspect the compiler script source (Perl `GetOptions` blocks)

**Critical constraint**: BIST should almost always be disabled for modern flows. Record the BIST disable method per family — it varies (e.g., TSMC uses `-NonBIST` in config or CLI).

## 1.7 Record Everything in a Registry

The end product of investigation is a **structured registry** — one entry per family:

```python
FAMILIES = {
    'spsram': FamilySpec(
        family_id='spsram',
        description='Single Port SRAM',
        compiler_version='130b',
        compiler_dir=Path('/data/foundry/.../SPSRAM/130b/'),
        script_name='tsn12ffcllspsram_130b.pl',
        comp_no='TSN12FFCLLSPSRAM',
        bitcell='LL',
        has_segment=True,
        default_tokens=frozenset({'w', 'a', 's', 'o', 'd', 'c', 'p'}),
        supported_tokens=frozenset({'w', 'a', 's', 'o', 'd', 'c', 'p', 'h', 'r'}),
        positive_flag_map={'h': '-DualRail', 'r': '-ColRed'},
        negative_flag_map={'w': '-NonBWEB', 'a': '-NonSLP', ...},
    ),
    # ... more families
}
```

This registry is the **single source of truth** for the entire wrapper system.
