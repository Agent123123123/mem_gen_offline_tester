# Step 7: Pitfalls & Lessons Learned

## Overview

This chapter collects hard-won lessons from building memory generators across different foundries and nodes. Each entry follows the pattern: **symptom → root cause → fix**.

---

## 7.1 Compiler Invocation

### Pitfall: Non-Zero Exit Code ≠ Failure

**Symptom**: Your wrapper reports every compilation as failed.

**Root cause**: Some compilers return non-zero exit codes for warnings or timeouts, but still produce correct output files.

**Fix**: Always check for artifact existence *in addition to* exit code:

```python
# WRONG
if proc.returncode != 0:
    raise CompilerError("compilation failed")

# RIGHT
if proc.returncode != 0:
    artifacts = list(output_dir.glob("*.v"))
    if artifacts:
        verdict = "SUCCESS"   # exit-code was just a warning
    else:
        verdict = "FAIL"      # genuine failure
```

### Pitfall: Timeout Value Too Aggressive

**Symptom**: Large memories (deep or wide) consistently "fail", but smaller ones succeed.

**Root cause**: Compilation time scales non-linearly with memory size. A 60-second timeout works for small macros but not for large ones.

**Fix**: Scale timeout with memory size, or use a generous default (e.g., 600 seconds) with `subprocess.run(..., timeout=...)`.

### Pitfall: Environment Not Loaded

**Symptom**: `command not found` when invoking the compiler.

**Root cause**: The compiler requires `module load` or specific `PATH`/`LD_LIBRARY_PATH` setup that your wrapper doesn't inherit.

**Fix**: Document the required environment setup. Either:
- Source the setup script inside your wrapper's shell invocation
- Check for the compiler binary at startup and fail fast with a clear message

```python
import shutil
if not shutil.which("mc2_com"):
    sys.exit("ERROR: mc2_com not found. Run 'module load mc2_n12/2013.12' first.")
```

---

## 7.2 Name Parsing

### Pitfall: VT Suffix Order Matters

**Symptom**: Parser misidentifies memory family or VT when multiple VT options exist.

**Root cause**: Foundry names embed VT codes (`ulvt`, `lvt`, `svt`, `hvt`) at varying positions, and shorter VT strings can be substrings of longer ones (`lvt` matches inside `ulvt`).

**Fix**: Match longest VT string first:

```python
# WRONG — "lvt" matches inside "ulvt"
VT_OPTIONS = ["lvt", "ulvt", "svt", "hvt"]
for vt in VT_OPTIONS:
    if vt in name: ...

# RIGHT — longest match first
VT_OPTIONS = ["ulvt", "lvt", "svt", "hvt"]  # ulvt before lvt
for vt in VT_OPTIONS:
    if vt in name:
        return vt
```

### Pitfall: Family Detection Ambiguity

**Symptom**: `dpsram` names incorrectly matched as `spsram` (because "spsram" is a substring of "dpsram"... no, but similar prefix issues exist).

**Root cause**: Prefix-based matching without proper boundary checks.

**Fix**: Use the foundry's documented prefix table and match in order of specificity (most specific first):

```python
# Order matters: check "hdspmbsram" before "spsram"
PREFIX_MAP = [
    ("shdspsb", "shdspsbsram"),
    ("shdsp",   "shdspmbsram"),
    ("spsram",  "spsram"),
    # ... etc
]
```

### Pitfall: Config Format Varies by Family

**Symptom**: Config file that works for SPSRAM fails for register files.

**Root cause**: Different memory families use different config key names, even within the same foundry:
- SPSRAM: `num_words`, `num_bits`
- RF: `word_depth`, `data_width`
- ROM: `rom_words`, `rom_bits`

**Fix**: Build a per-family config template registry:

```python
CONFIG_TEMPLATES = {
    "spsram": {"key_words": "num_words", "key_bits": "num_bits", ...},
    "1prf":   {"key_words": "word_depth", "key_bits": "data_width", ...},
}
```

---

## 7.3 Tiling

### Pitfall: Non-Power-of-2 Depth Padding

**Symptom**: Address decode fails — writes to last few addresses hit wrong tile or nothing.

**Root cause**: When `exposed_depth` is not evenly divisible by `child_words`, the last vertical tile has fewer valid addresses. The address decode logic must handle this carefully.

**Fix**: Use priority-based address decode, not pure bit-slicing:

```python
# WRONG — simple bit decode assumes equal-sized tiles
row_sel = addr >> log2(child_words)

# RIGHT — compare against base addresses
if addr < 1 * child_words:
    row_sel = 0
elif addr < 2 * child_words:
    row_sel = 1
elif addr < 2 * child_words + last_tile_words:
    row_sel = 2
else:
    row_sel = INVALID
```

### Pitfall: Read Data Mux Timing

**Symptom**: Read data comes from the wrong tile — data appears "shifted" by one cycle.

**Root cause**: SRAM reads are registered — the data appears one cycle after the address is presented. But the address (and thus `row_sel`) may change by the next cycle.

**Fix**: Register `row_sel` for one cycle and use the delayed version to mux read data:

```verilog
always @(posedge CLK) begin
    row_sel_d <= row_sel;   // 1-cycle delay
end

// Use row_sel_d (not row_sel) for read data mux
assign Q = (row_sel_d == 0) ? q_tile_0 :
           (row_sel_d == 1) ? q_tile_1 :
                              q_tile_2;
```

### Pitfall: Forgetting Edge Padding on Write Data

**Symptom**: Lint warning about undriven bits, or simulation shows X on padded bits.

**Root cause**: If the last horizontal tile is wider than needed, the extra bits must be tied to 0 on the write data input.

**Fix**: Zero-pad write data to the full tile width:

```verilog
// exposed_width=40, last tile is 16-bit, only bits [7:0] used
assign tile_2_D = {8'b0, D[39:32]};  // pad upper 8 bits
```

---

## 7.4 RTL Generation

### Pitfall: UHDL Port Name Case Sensitivity

**Symptom**: Generated wrapper has ports like `clk` but macro expects `CLK`.

**Root cause**: Port names are case-sensitive in Verilog. The RTL generator must use the exact port names from the compiler-generated model.

**Fix**: Extract port names from the compiler output, don't assume them:

```python
# Parse the compiler-generated Verilog to get exact port names
ports = extract_ports(compiler_output_verilog)
# Use those exact names in wrapper connections
```

### Pitfall: Missing `filelist.f` Entries

**Symptom**: Downstream synthesis/simulation can't find the compiler-generated models.

**Root cause**: `filelist.f` only lists your generated wrapper files but not the compiler-generated `.v` models.

**Fix**: Include both in the filelist:

```
// Compiler-generated models
./compiler_output/ts5n12ffcllulvta8x16m1swsho.v

// Generated wrappers
./wrapper_rtl/my_rf.v
./wrapper_rtl/my_rf_tile.v
```

---

## 7.5 BIST and Test Options

### Pitfall: BIST Enable Causes Port Explosion

**Symptom**: Your wrapper's port map doesn't match — dozens of unexpected BIST ports.

**Root cause**: When BIST is enabled, the compiler adds many additional ports (`BIST_EN`, `BIST_ADDR`, `BIST_CLK`, `BIST_D`, etc.) that your wrapper doesn't know about.

**Fix**: Disable BIST by default. Modern SoC flows use external BIST controllers, not embedded BIST:

```python
# Permanent constraint in config
"bist": "off",
"power_gating": "off",
```

If BIST is required, add BIST port passthrough to the wrapper generator as a separate mode.

---

## 7.6 Cross-Foundry Portability

### Pitfall: Hardcoded Foundry Paths

**Symptom**: Moving to a new foundry requires editing dozens of files.

**Root cause**: Foundry paths (`/tools/foundry/tsmc/...`) and module names (`mc2_n12`) scattered throughout the code.

**Fix**: Centralize all foundry-specific paths in one config file:

```json
{
  "foundry": "tsmc",
  "node": "12nm",
  "compiler_bin": "/tools/foundry/tsmc/mc2/bin/mc2_com",
  "module_load": "mc2_n12/2013.12",
  "output_base": "/data/memory_output"
}
```

### Pitfall: Assuming Single-Segment Naming

**Symptom**: Parser works for TSMC but breaks on Samsung or GF names.

**Root cause**: Different foundries use fundamentally different naming schemes:
- TSMC: single flat string (`ts5n12ffcllulvta8x16m1swsho`)
- Samsung: hierarchical (`S55NLLX_RG2P_...`)
- GF: dash-separated (`GF12LP-SRAM-SP-256x32-...`)

**Fix**: Use the abstraction layer from Chapter 2 — a foundry-specific adapter that translates names to a common `MemorySpec` object.

---

## 7.7 Performance

### Pitfall: Sequential Compilation in Batch Mode

**Symptom**: Generating 50 memories takes hours.

**Root cause**: Each compilation runs sequentially, and many compilations are license-limited anyway.

**Fix**: Use process-level parallelism with license-aware throttling:

```python
from concurrent.futures import ProcessPoolExecutor

MAX_LICENSES = 4  # Check your license server
with ProcessPoolExecutor(max_workers=MAX_LICENSES) as pool:
    futures = [pool.submit(compile_one, name) for name in memory_list]
    results = [f.result() for f in futures]
```

---

---

## §7.X [CRITICAL] mc2-eu GUI Mode Hang — Compiler Produces No Output

**Symptom**: `perl <family>.pl -file config.txt -VERILOG ...` completes in <0.1s,
produces only a `.cfg` file, no `VERILOG/` subdirectory created, no `mc.log`.

**Root cause**: `bin/mc2-eu` (found in PATH via `module load mc2_n12/2013.12`) is
a **wrapper shell script** that symlinks to `.wrapper`. When `mc2-eu` detects that
stdin is a TTY, it tries to launch in GUI mode. Without a display or in a non-
interactive environment, it exits immediately without compiling.

The **real binary** is at `$MC2_INSTALL_DIR/bin/Linux-64/mc2-eu`. This binary
always runs in textual mode.

**Fix**: When invoking MC from Python, prepend `bin/Linux-64` to PATH and set
`MC2_INSTALL_DIR` explicitly. Call `perl` directly (do NOT use `bash run.sh`):

```python
MC2_DIR = Path("/data/eda/tsmc/memory_compiler/tsmc_n12ffcllmc_20131200_100a/MC2_2013.12.00.f")
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
    capture_output=True,   # ← REQUIRED: prevents GUI detection via TTY
    text=True,
    timeout=600,
)
```

**Verification**: A successful compile takes ~30 seconds and produces:
- `<work_dir>/mc.log` with `MC2 : Memory Compiler Software` header
- `<work_dir>/<memory_name>_<version>/VERILOG/<memory_name>_<version>.v`

**Do NOT** use `['bash', 'run.sh']` — the `module load` inside `run.sh` does not
re-export `PATH` changes when bash is started as a non-interactive subprocess.

---

## §7.Y config.txt Format — Do NOT Put Flags in config.txt for 1prf/spsram/dpsram

**Symptom**: `[Error] Option setting in config.txt file cannot be executed by gen perl script followed by options (e.g. <compiler_name>.pl -NonBIST -NonSD )`

**Root cause**: These families require `-NonBIST`, `-NonSLP`, `-NonDSLP`, `-NonSD`
to be passed as **command-line arguments** to the perl script, NOT written into
`config.txt`.

**Correct config.txt** (just the size spec, e.g.):
```
8x16m1s
```

**Correct perl invocation**:
```bash
perl tsn12ffcll1prf_130c.pl -file config.txt -VERILOG -DATASHEET -NonBIST -NonSLP -NonDSLP -NonSD
```

---

## Summary: Top 12 Rules

| # | Rule |
|---|------|
| 1 | Always check artifacts, never trust exit codes alone |
| 2 | Match VT strings longest-first |
| 3 | Config key names vary by family — use a template registry |
| 4 | Register `row_sel` for read mux — SRAMs have 1-cycle read latency |
| 5 | Zero-pad write data to full tile width |
| 6 | Disable BIST by default |
| 7 | Extract port names from compiler output, don't hardcode |
| 8 | Include compiler models in `filelist.f` |
| 9 | Centralize all foundry-specific paths in one config |
| 10 | Scale compiler timeout with memory size |
| 11 | **MC invocation: prepend `bin/Linux-64` to PATH, use `capture_output=True`** |
| 12 | **config.txt: size spec only; BIST/power flags go on perl command line** |
