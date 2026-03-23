# Step 8: Decision Log

## Overview

This document records key architectural decisions made when building the memory generator. Each entry follows the **ADR (Architecture Decision Record)** format:
- **Context** — Why did this decision come up?
- **Options** — What alternatives were considered?
- **Decision** — What was chosen and why?
- **Consequences** — What are the trade-offs?

---

## ADR-001: Two-Layer Wrapper Architecture

**Context**: A tiled memory requires a top-level wrapper that stitches multiple macro instances together. How should this be structured?

**Options**:
1. **Flat wrapper** — one module instantiates all tiles directly
2. **Two-layer** — a "tile wrapper" normalizes each macro, then a "top wrapper" stitches tile wrappers

**Decision**: **Two-layer**. The tile wrapper handles port renaming and per-tile signal gating. The top wrapper only does address decode and data mux.

**Consequences**:
- (+) Each layer is testable independently
- (+) Tile wrapper is reusable across different tiling configurations
- (+) Address decode logic stays simple — operates on tile-wrapper interfaces, not raw macro ports
- (−) One extra hierarchy level in the design

---

## ADR-002: Python HDL vs. String Templates for RTL Generation

**Context**: Generated Verilog wrapper files need to be produced programmatically. Two approaches exist.

**Options**:
1. **String templates** (Jinja2, f-strings) — directly construct Verilog text
2. **Python HDL framework** (UHDL, Amaranth, PyRTL) — build a hardware model, then emit Verilog

**Decision**: **Python HDL (UHDL in the TSMC 12nm demo)**.

**Consequences**:
- (+) Width consistency enforced by the framework — can't accidentally connect 8-bit to 16-bit
- (+) Structural manipulation (port renaming, slicing) is safer than string surgery
- (+) The framework can emit different formats (Verilog, VHDL, SystemVerilog) from the same model
- (−) Additional dependency (UHDL framework)
- (−) Learning curve for the HDL API
- (−) String templates are simpler for very basic wrappers

**Note**: If your project doesn't want an HDL dependency, string templates are acceptable for simple one-layer wrappers. But as tiling complexity grows, an HDL framework pays for itself.

---

## ADR-003: Disable BIST by Default

**Context**: Memory compilers can generate macros with embedded BIST (Built-In Self-Test). Should we enable it?

**Options**:
1. **Enable BIST** — compiler adds BIST ports and logic
2. **Disable BIST** — compiler generates clean macros without BIST

**Decision**: **Disable BIST by default**.

**Consequences**:
- (+) Macro port list stays clean — only functional ports (CLK, A, D, Q, CS, WE, etc.)
- (+) Wrapper generator doesn't need to handle BIST port passthrough
- (+) Area is smaller
- (−) If downstream flow requires embedded BIST, extra work needed
- **Rationale**: Modern SoC memory BIST is typically handled by an external BIST controller (e.g., Synopsys STAR Memory System) that wraps around the macros. Embedded BIST is rarely used in practice.

---

## ADR-004: Differential Flag Pattern for Config Generation

**Context**: Memory compiler config files have many parameters. Most have sensible defaults. How to manage the configuration?

**Options**:
1. **Full-spec config** — specify every parameter for every compilation
2. **Differential flag pattern** — maintain a base template, apply only user-specified overrides

**Decision**: **Differential flag pattern**.

```python
base_config = load_family_template(family)  # 40+ default parameters
user_overrides = {"words": 256, "bits": 32}
final_config = {**base_config, **user_overrides}
```

**Consequences**:
- (+) User only specifies what differs from defaults
- (+) Base templates encode foundry best practices
- (+) Easy to audit — `diff base_config final_config` shows exactly what changed
- (−) Must maintain base templates per family (but these rarely change)

---

## ADR-005: Subcommand CLI with Offline-First Design

**Context**: The tool needs a user interface. How should it be structured?

**Options**:
1. **Single command** — `memgen --name X --width W --depth D --output O`
2. **Subcommand pattern** — `memgen check|plan|generate|run`
3. **Config-file driven** — `memgen --config mem.yaml`

**Decision**: **Subcommand pattern with offline-first design**.

**Consequences**:
- (+) `families`, `check`, `plan` work without compiler access — usable on any laptop
- (+) Progressive complexity — start with `check`, graduate to `run`
- (+) Each subcommand has focused help and error messages
- (+) Machine-readable `--json` output enables CI scripting
- (−) More code than a single-command interface

---

## ADR-006: Compiler Exit Code Handling — Check Artifacts, Not Return Code

**Context**: Memory compiler processes sometimes return non-zero exit codes for warnings or timeout, but still produce correct output.

**Options**:
1. **Trust exit code** — non-zero = failure
2. **Check artifacts** — scan output directory for expected files regardless of exit code
3. **Both** — check exit code, then verify artifacts, use combined verdict

**Decision**: **Option 3 — both, with artifacts taking priority**.

```
EXIT=0 + artifacts present  → SUCCESS
EXIT≠0 + artifacts present  → SUCCESS (with warning logged)
EXIT=0 + artifacts missing  → FAIL (silent corruption)
EXIT≠0 + artifacts missing  → FAIL
```

**Consequences**:
- (+) Never misses a successful compilation that had warnings
- (+) Catches silent corruption (zero-byte files) even on EXIT=0
- (−) May accept output from a partially failed compilation (mitigated by file-size checks)

---

## ADR-007: Pure-Math Tiling Engine with No Side Effects

**Context**: The tiling calculator needs to determine how many tiles are needed and how bits/addresses map. Should it also write files?

**Options**:
1. **Tiling + file generation** in one module
2. **Pure tiling math** (returns `TileMapping` data structure) → separate file writer

**Decision**: **Pure math, no side effects**.

**Consequences**:
- (+) Tiling engine is easily unit-testable (input → output, no filesystem)
- (+) Can be used in `memgen plan` without writing anything
- (+) Can be imported by other tools (area estimators, PPA predictors)
- (−) Requires a separate "emit" step to produce RTL

---

## ADR-008: Per-Family Config Template Registry

**Context**: Different memory families use different config file formats, key names, and valid ranges.

**Options**:
1. **Single generic parser** — try to handle all formats
2. **Per-family template + registry** — each family has its own config template and parameter mapping

**Decision**: **Per-family registry**.

```python
FAMILY_REGISTRY = {
    "spsram":  {"template": "spsram.cfg.j2",  "key_words": "num_words", ...},
    "1prf":    {"template": "1prf.cfg.j2",    "key_words": "word_depth", ...},
    "dpsram":  {"template": "dpsram.cfg.j2",  "key_words": "num_words", ...},
}
```

**Consequences**:
- (+) Each family's quirks are isolated — changes to SPSRAM config don't break RF config
- (+) Adding a new family = adding one registry entry + one template
- (+) Clear documentation of what each family expects
- (−) Some duplication across templates (mitigated by template inheritance if needed)

---

## ADR-009: Request Record (request.json) for Traceability

**Context**: When debugging a generated wrapper months later, how do you know what parameters were used?

**Options**:
1. **No record** — trust the user to remember
2. **Log file** — write compilation log
3. **Structured request record** — JSON with all inputs, outputs, and timestamps

**Decision**: **Structured request.json** written alongside every compilation output.

```json
{
  "memory_name": "ts5n12ffcllulvta8x16m1swsho",
  "family": "1prf",
  "words": 8, "bits": 16,
  "exposed_width": 40, "exposed_depth": 20,
  "tiles_h": 3, "tiles_v": 3,
  "timestamp": "2024-01-15T10:30:00Z",
  "memgen_version": "0.1.0",
  "compiler": "mc2_com",
  "config_file": "memory.cfg"
}
```

**Consequences**:
- (+) Full reproducibility — anyone can re-run the exact same compilation
- (+) Machine-parseable — CI can aggregate results
- (+) Serves as documentation for each generated memory
- (−) One extra file per compilation (negligible cost)

---

## Template: Adding New Decisions

When you make a significant architectural choice, add it here using this template:

```markdown
## ADR-NNN: <Title>

**Context**: <What situation prompted this decision?>

**Options**:
1. <Option A> — <brief description>
2. <Option B> — <brief description>

**Decision**: <Which option was chosen?>

**Consequences**:
- (+) <Benefit>
- (−) <Drawback>
```

A decision is worth recording if:
- It affects multiple files or modules
- It's non-obvious (someone might reasonably choose differently)
- Reversing it later would require significant rework
