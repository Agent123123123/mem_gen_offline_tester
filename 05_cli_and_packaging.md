# Step 5: CLI Design & Packaging

## Overview

The wrapper and tiling engine need a user-facing interface. A well-designed CLI makes the difference between a tool that gets adopted and one that sits unused.

## 5.1 CLI Architecture — Subcommand Pattern

Use a subcommand-based CLI (like `git`, `docker`, `kubectl`):

```
memgen <command> [options]
```

### Recommended Subcommands

| Command | Purpose | Compiler needed? |
|---------|---------|-----------------|
| `families` | List supported memory families | No |
| `check <name>` | Parse & validate a memory name | No |
| `plan <name> --width W --depth D` | Preview tiling layout | No |
| `generate <name> --kits ...` | Run compiler, produce macro files | Yes |
| `run <name> <top> --width W --depth D` | Full pipeline: compiler + RTL wrapper | Yes |

**Design principle**: The first three commands require **no compiler, no license, no EDA environment**. Users can validate names and preview tiling plans on any laptop. This lowers the adoption barrier dramatically.

## 5.2 Help Design

Every subcommand should have:

1. **One-line summary** (shown in `memgen --help`)
2. **Full description** (shown in `memgen <cmd> --help`)
3. **Examples** (concrete invocation patterns)

### Root Help Template

```
usage: memgen [-h] [--version] <command> ...

memgen — <Foundry> <Node> Memory Generator CLI

A unified command-line interface for the <Foundry> <Node> memory compiler
wrapper and automatic RTL wrapper generator.

Supported memory families:
  spsram          Single Port SRAM
  dpsram          Dual Port SRAM
  1prf            1-Port Register File
  ...

commands:
  families    List all supported memory families
  check       Parse and validate a memory name (no compiler needed)
  plan        Preview tiling plan for a width/depth (no compiler needed)
  generate    Invoke memory compiler to produce macro files
  run         Full pipeline: compiler + RTL wrapper generation

Quick start:
  memgen families
  memgen check   ts5n12ffcllulvta8x16m1swsho
  memgen plan    ts5n12ffcllulvta8x16m1swsho --width 40 --depth 20
  memgen generate ts5n12ffcllulvta8x16m1swsho --kits DATASHEET VERILOG
  memgen run     ts5n12ffcllulvta8x16m1swsho my_rf --width 40 --depth 20
```

### Subcommand Help Template

```
usage: memgen plan [-h] --width WIDTH --depth DEPTH [--json] memory_name

Calculate how many macro tiles are needed to implement the requested
width × depth, and show each tile's data-bit and address mapping.

No files are written, no compiler is invoked.

positional arguments:
  memory_name      TSMC-convention memory name

options:
  --width WIDTH    Desired logical data width (bits)
  --depth DEPTH    Desired logical depth (words)
  --json           Output full plan in JSON format

Examples:
  memgen plan ts5n12ffcllulvta8x16m1swsho --width 40 --depth 20
  memgen plan ts5n12ffcllulvta8x16m1swsho --width 128 --depth 512 --json
```

## 5.3 Output Modes

Every read-only subcommand should support two output modes:

```python
# Human-readable (default)
memgen check ts5n12ffcllulvta8x16m1swsho
#   Memory name : ts5n12ffcllulvta8x16m1swsho
#   Family      : 1prf
#   Words × Bits: 8 × 16
#   ...

# Machine-readable (--json)
memgen check ts5n12ffcllulvta8x16m1swsho --json
#   {"raw_name": "ts5n12ffcllulvta8x16m1swsho", "family": "1prf", ...}
```

**Why JSON?** Enables CI/CD scripting — `memgen check --json | jq .family`

## 5.4 Packaging with pyproject.toml

```toml
[build-system]
requires = ["setuptools>=42", "wheel"]
build-backend = "setuptools.build_meta"

[project]
name = "memory-generator-<foundry>-<node>"
version = "0.1.0"
requires-python = ">=3.10"

[project.scripts]
memgen = "memgen.cli:main"
```

After `pip install -e .`, the `memgen` command is available system-wide.

## 5.5 Module Organization

```
memgen/
├── __init__.py       # Version
├── cli.py            # argparse-based CLI (all subcommands)
├── wrapper.py        # Compiler wrapper (name parse, config gen, invocation)
├── plan.py           # Pure tiling calculator (no side effects)
├── uhdl_emit.py      # RTL generator (UHDL-based wrapper code gen)
└── generate.py       # Full pipeline helper
```

**Key separation**:
- `plan.py` has **zero dependencies** (no compiler, no UHDL, no file I/O) — pure math
- `wrapper.py` depends on foundry paths but not UHDL
- `uhdl_emit.py` depends on UHDL framework
- `cli.py` ties everything together with lazy imports for fast startup

## 5.6 Exit Code Convention

| Code | Meaning |
|------|---------|
| 0 | Success |
| 1 | User error (invalid name, bad arguments) |
| 2 | Compiler error (non-zero exit, missing output) |
| 3 | Wrapper generation error (UHDL failure, missing model) |

```python
def main() -> int:
    try:
        parser = build_parser()
        args = parser.parse_args()
        return args.func(args)
    except WrapperError as e:
        print(f"[ERROR] {e}", file=sys.stderr)
        return 1
```

## 5.7 CI / Automation Integration

The CLI is designed for scripting:

```bash
#!/bin/bash
# CI pipeline: generate all memories in a memlist
while IFS= read -r mem_name width depth; do
    memgen run "$mem_name" "${mem_name}_wrapper" \
        --width "$width" --depth "$depth" \
        --output-dir "$BUILD_DIR/$mem_name"
    if [ $? -ne 0 ]; then
        echo "FAIL: $mem_name" >> errors.log
    fi
done < memlist.txt
```

### Batch memlist.txt Format

```
ts5n12ffcllulvta8x16m1swsho   40   20
ts6n12ffcllulvta8x12m1fwsho   64   128
tsn12ffcll256x32m4swbasodcp   256  1024
```

## 5.8 Versioning

Use semantic versioning:
- **0.x.y**: Pre-1.0, breaking changes allowed
- **1.0.0**: Stable CLI interface — breaking changes require major bump
- `memgen --version` should always work
