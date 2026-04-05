# Step 3: Designing the Compiler Wrapper

## Overview

The wrapper layer sits between the user and the raw foundry compiler. Its job:
1. Accept a high-level request (memory name or parameters)
2. Generate correct config files
3. Invoke the compiler with proper environment
4. Validate outputs
5. Produce reproducible artifacts

## 3.1 Architecture

```
User request
     │
     ▼
┌────────────────┐
│  Name Parser   │  ← Decode name → MemorySpec
├────────────────┤
│  Constraint    │  ← Validate against family rules
│  Validator     │
├────────────────┤
│  Config        │  ← Generate config.txt + flags
│  Generator     │
├────────────────┤
│  Artifact      │  ← Write config.txt, run.sh, request.json
│  Writer        │
├────────────────┤
│  Compiler      │  ← module load + invoke compiler script
│  Runner        │
├────────────────┤
│  Output        │  ← Verify expected outputs exist
│  Validator     │
└────────────────┘
     │
     ▼
  Output kits
```

## 3.2 Config Generation Strategy

### The Differential Flag Pattern

Foundry compilers have default-ON and default-OFF features. The key insight:

```python
def config_flags(family: FamilySpec, spec: MemorySpec) -> list[str]:
    """Generate only the DIFFERENTIAL flags — what differs from defaults."""
    flags = []
    
    # Default-ON features NOT in user's request → disable them
    for token, flag in family.negative_flag_map.items():
        if token in family.default_tokens and token not in spec.options:
            flags.append(flag)   # e.g., "-NonBWEB"
    
    # Default-OFF features IN user's request → enable them
    for token, flag in family.positive_flag_map.items():
        if token not in family.default_tokens and token in spec.options:
            flags.append(flag)   # e.g., "-DualRail"
    
    # Always inject: BIST disable (permanently)
    if family.bist_disable_mode == 'config':
        flags.append('-NonBIST')
    
    return flags
```

### Permanent Constraints

Some features should **always** be in a specific state regardless of user request:

```python
PERMANENT_CONSTRAINTS = {
    'bist':  'disabled',   # BIST interferes with SoC DFT — always disable
    'rom':   'disabled',   # ROM family deprecated in our flow
}
```

**Why disable BIST permanently?**
- Modern SoC flows use external MBIST controllers, not compiler-embedded BIST
- Compiler BIST adds area, timing, and routing overhead
- Verification team doesn't test compiler BIST — only block-level MBIST

### Config File Format

```python
def write_config(run_dir: Path, spec: MemorySpec, family: FamilySpec):
    config_line = " ".join([spec.base_config, *config_flags(family, spec)])
    (run_dir / 'config.txt').write_text(config_line + '\n')
```

## 3.3 Shell Command Generation

Generate a `run.sh` that can reproduce the compiler run independently:

```python
def build_shell_command(family, config_path, module_name, kits):
    parts = [
        f'module load {module_name} &&',
        f'perl {family.script_path}',
        f'-file {config_path}',
    ]
    # Add kit flags
    for kit in kits:
        parts.append(f'-{kit}')
    
    # BIST disable via CLI (for families that require it)
    if family.bist_disable_mode == 'cli':
        parts.append('-NonBIST')
    
    return ' '.join(parts)
```

## 3.4 Request Record

Every run should generate a machine-readable record:

```python
def write_request(run_dir, family, spec, kits, wrapper_info=None):
    payload = {
        'memory_name': spec.raw_name,
        'family': family.family_id,
        'compiler_version': family.compiler_version,
        'config': spec.base_config,
        'flags': config_flags(family, spec),
        'kits': kits,
        'timestamp': datetime.now().isoformat(),
    }
    if wrapper_info:
        payload['wrapper'] = wrapper_info
    
    (run_dir / 'request.json').write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + '\n'
    )
```

**Why?** Traceability. When debugging a memory issue 6 months later, `request.json` tells you exactly what was requested and how it was configured.

## 3.5 Compiler Invocation

> **CRITICAL**: Use the proven pattern below. `bash run.sh` (with `module load` inside)
> does NOT work reliably when called from a Python subprocess — `module` is not available
> in non-interactive bash, and `bin/mc2-eu` is a wrapper script that triggers GUI mode
> when stdin is a TTY. Always call `perl` directly with explicit env.

```python
MC2_DIR = Path("/data/eda/tsmc/memory_compiler/tsmc_n12ffcllmc_20131200_100a/MC2_2013.12.00.f")

def run_compiler(work_dir: Path, family: FamilySpec) -> subprocess.CompletedProcess:
    script_path = family.compiler_dir / family.script_name
    env = os.environ.copy()
    env['MC2_INSTALL_DIR'] = str(MC2_DIR)
    env['MC_HOME']         = str(family.compiler_dir)
    env['PATH']            = str(MC2_DIR / 'bin' / 'Linux-64') + ':' + env.get('PATH', '')

    result = subprocess.run(
        ['perl', str(script_path),
         '-file', 'config.txt',
         '-VERILOG', '-DATASHEET',
         '-NonBIST', '-NonSLP', '-NonDSLP', '-NonSD'],
        cwd=str(work_dir),
        env=env,
        capture_output=True,   # ← REQUIRED: prevents GUI mode detection via TTY
        text=True,
        timeout=600,
    )
    # Always save log regardless of success/failure
    (work_dir / 'mc.log').write_text(result.stdout + result.stderr)
    return result
```

A successful compile (~30s) produces `<work_dir>/<memory_name>_<version>/VERILOG/`.
See `ref_code/memgen_reference.py` for a fully-working reference implementation.

### Environment Considerations

| Concern | How to handle |
|---------|--------------|
| mc2-eu binary | Use `bin/Linux-64/mc2-eu` (real binary), NOT `bin/mc2-eu` (wrapper) |
| PATH setup | Prepend `$MC2_INSTALL_DIR/bin/Linux-64` before calling perl |
| TTY / GUI mode | Always use `capture_output=True` in subprocess.run |
| License contention | Retry with backoff, or check `lmstat` first |
| Timeout | 600s default; scale larger for big memories |
| Parallel runs | Safe if each run uses its own directory |

## 3.6 Output Validation

After compiler finishes, verify outputs:

```python
def validate_outputs(run_dir, spec, kits):
    output_dir = run_dir / spec.output_name
    
    for kit in kits:
        kit_dir = output_dir / kit.upper()
        if not kit_dir.is_dir():
            raise RuntimeError(f"Expected kit output not found: {kit_dir}")
        if not any(kit_dir.iterdir()):
            raise RuntimeError(f"Kit directory is empty: {kit_dir}")
    
    # Specifically for VERILOG: check .v file exists
    if 'VERILOG' in kits:
        v_file = output_dir / 'VERILOG' / f'{spec.output_name}.v'
        if not v_file.is_file():
            raise RuntimeError(f"Verilog model not found: {v_file}")
```

## 3.7 Error Handling Strategy

```
Compiler exit code 0 + output exists  → SUCCESS
Compiler exit code 0 + no output      → PARTIAL (warn, output may be incomplete)
Compiler exit code != 0 + output      → CHECK (compiler error but partial output)
Compiler exit code != 0 + no output   → FAIL (report error, show log tail)
Timeout                                → TIMEOUT (check if partial output exists)
```

**Never assume timeout = failure** — some compilers write output files before printing the final status line. Always check for artifacts.

## 3.8 Multi-Family Design Patterns

When supporting multiple families, use a registry + dispatch pattern:

```python
FAMILIES = {
    'spsram': spsram_spec,
    'dpsram': dpsram_spec,
    '1prf':   prf1_spec,
    '2prf':   prf2_spec,
}

def resolve_family(memory_name, override=None):
    if override:
        return FAMILIES[override]
    
    # Auto-detect from name prefix
    for fid, spec in FAMILIES.items():
        if matches_prefix(memory_name, spec):
            return spec
    
    raise WrapperError(f"Cannot detect family for: {memory_name}")
```

**Key principle**: Each family is a data description (FamilySpec), not a code branch. Adding a new family means adding one registry entry, not writing new control flow.
