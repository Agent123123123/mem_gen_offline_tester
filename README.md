# Memory Generator — Universal Build Guide

## What This Is

A complete methodology package for building a **production-quality Memory Generator** from scratch — given access to any foundry's Memory Compiler, automatically generate compiler wrappers, RTL stitch wrappers, and CI-ready tooling.

This guide is **foundry-agnostic** and **memory-type-agnostic**. It covers SRAM, Register Files, ROM, Cache macros, eFuse, and any macro that has a foundry-provided batch-mode compiler.

## Target Audience

An AI agent (or human engineer) who:
- Has access to a foundry Memory Compiler (TSMC, Samsung, Intel, SMIC, GlobalFoundries, etc.)
- At **any process node** (3nm, 5nm, 7nm, 12nm, 16nm, 22nm, 28nm, etc.)
- Wants to build a **unified CLI wrapper + RTL stitching generator** that the rest of the team can use
- Has no pre-existing infrastructure — must build everything from investigation through deployment

## Folder Structure

```
mem_gen_offline_tester/
├── README.md                          ← You are here
├── 01_investigation.md                ← How to investigate a new MC installation
├── 02_name_convention.md              ← Decode foundry naming conventions
├── 03_wrapper_design.md               ← Design the compiler wrapper layer
├── 04_tiling_and_stitching.md         ← RTL wrapper generation & tiling algorithm
├── 05_cli_and_packaging.md            ← CLI design, packaging, CI integration
├── 06_validation.md                   ← Verification strategy for generated RTL
├── 07_pitfalls.md                     ← Lessons learned & critical failure modes
├── 08_decision_log.md                 ← Key architectural decisions & rationale
├── ref_code/
│   ├── name_parser_skeleton.py        ← Reference name parser (foundry-agnostic)
│   └── tiling_engine_skeleton.py      ← Reference tiling calculator
└── examples/
    └── tsmc_12nm_sram/                ← Submodule: proven TSMC 12nm implementation
```

## What a Memory Generator Does

```
User gives:                    Memory Generator:                Output:
┌─────────────────┐            ┌──────────────────┐            ┌─────────────────────┐
│ • Memory name   │            │ 1. Parse name     │            │ • Verilog model     │
│   or params     │──────────▶ │ 2. Gen config     │──────────▶ │ • LEF / GDS / LIB   │
│ • Width × Depth │            │ 3. Run compiler   │            │ • RTL wrapper       │
│ • Output kits   │            │ 4. Stitch wrapper │            │ • Filelist.f        │
└─────────────────┘            └──────────────────┘            └─────────────────────┘
```

Key value-add vs. raw compiler usage:
- **Naming abstraction**: User gives a human-readable spec, not low-level config lines
- **Constraint enforcement**: BIST disabled, invalid combos rejected early
- **RTL auto-stitching**: Need 128×2048 but macro is 16×64? Auto-tile to 8×32 macros + wrapper
- **Reproducibility**: Every run generates `config.txt`, `run.sh`, `request.json`

## End-to-End Pipeline

```
┌──────────────┐     ┌──────────────┐     ┌──────────────┐
│ 1. Investigate│────▶│ 2. Decode    │────▶│ 3. Design    │
│    MC install │     │    naming    │     │    wrapper   │
└──────────────┘     └──────────────┘     └──────────────┘
                                                │
┌──────────────┐     ┌──────────────┐     ┌──────────────┐
│ 6. Validate  │◀────│ 5. Package   │◀────│ 4. Build     │
│    & deploy  │     │    CLI       │     │    tiling    │
└──────────────┘     └──────────────┘     └──────────────┘
```

1. **Investigate** the MC installation — discover families, scripts, options, valid ranges
2. **Decode** the foundry naming convention — build a parser
3. **Design** the compiler wrapper — config generation, flag mapping, constraint enforcement
4. **Build** the tiling / stitching engine — auto-expand width & depth beyond single-macro limits
5. **Package** into a CLI with help, subcommands, JSON output
6. **Validate** generated RTL; deploy to team

## Key Numbers (Proven on TSMC 12nm)

| Metric | Value |
|--------|-------|
| Families supported | 6 (spsram, dpsram, 1prf, 2prf, uhd1prf, uhd2prf) |
| Naming tokens parsed | 15 categories (VT, words, bits, mux, segment, options…) |
| Wrapper tiling tested | Up to 32×32 tile arrays |
| RTL generation time | <2s per wrapper (UHDL-based) |
| CLI subcommands | 5 (families, check, plan, generate, run) |

## How to Use This Guide

1. Read `01_investigation.md` — learn how to scan a new MC installation
2. Read `02_name_convention.md` — understand name decoding strategies
3. Read `03_wrapper_design.md` — design decisions for the compiler wrapper
4. Read `04_tiling_and_stitching.md` — the stitching algorithm in detail
5. Read `05_cli_and_packaging.md` — how to expose it as a CLI
6. Read `06_validation.md` — how to verify correctness
7. Read `07_pitfalls.md` **before starting** — saves days of debugging
8. Read `08_decision_log.md` — understand why things are designed this way
9. Study `examples/tsmc_12nm_sram/` — a complete working implementation
