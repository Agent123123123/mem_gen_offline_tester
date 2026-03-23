# Step 6: Validation & Quality Assurance

## Overview

A memory generator is only useful if the output is correct, lint-clean, and matches specifications. This chapter defines the validation strategy from three angles: **compiler output validation**, **RTL wrapper correctness**, and **end-to-end integration**.

---

## 6.1 Compiler Output Validation

After invoking the memory compiler, check (in order):

### 6.1.1 Exit-Code vs. Artifact Check

> **Rule**: Never trust the compiler exit code alone.

Some compilers report timeout or warnings with non-zero exit codes but still produce correct output. Always check artifacts:

```python
def validate_compiler_output(output_dir: Path, kits: list[str]) -> str:
    """Return verdict: SUCCESS / PARTIAL / FAIL."""
    expected_patterns = {
        "VERILOG":    "*.v",
        "DATASHEET":  "*.ds",
        "LEF":        "*.lef",
        "GDS":        "*.gds",
        "LIB":        "*.lib",
    }
    found, missing = [], []
    for kit in kits:
        pattern = expected_patterns.get(kit, f"*.{kit.lower()}")
        matches = list(output_dir.glob(pattern))
        (found if matches else missing).append(kit)

    if not missing:
        return "SUCCESS"
    elif found:
        return "PARTIAL"
    else:
        return "FAIL"
```

### 6.1.2 File-Size Sanity Check

A 0-byte `.v` or `.lib` file is a silent failure. Flag any output file below a reasonable threshold:

```python
MIN_FILE_SIZES = {
    ".v":   500,     # Verilog model should be substantial
    ".lib": 1000,    # Liberty file has header even if empty
    ".lef": 200,     # LEF has header
    ".gds": 100,     # Binary — even header > 100 bytes
}
```

### 6.1.3 Instance Name Cross-Check

The compiler-generated Verilog module name must match what the tiling engine expects:

```python
import re

def extract_module_name(verilog_file: Path) -> str:
    """Extract the first 'module XXX' declaration."""
    text = verilog_file.read_text()
    m = re.search(r'^\s*module\s+(\w+)', text, re.MULTILINE)
    if not m:
        raise ValueError(f"No module found in {verilog_file}")
    return m.group(1)

# Cross-check
assert extract_module_name(v_file) == expected_module_name
```

---

## 6.2 RTL Wrapper Validation

### 6.2.1 Lint Check

Run a Verilog linter on every generated wrapper:

```bash
# Verilator lint-only mode
verilator --lint-only -Wall --top-module <wrapper_name> \
    -f filelist.f 2>&1 | tee lint_report.txt

# Or use svlinter, if available
svlinter -f filelist.f
```

**Zero warnings** is the target. Common mistakes the linter catches:
- Undriven ports on unused tile slots (edge padding)
- Width mismatches at tile boundaries
- Missing sensitivity list entries (for `always @*`)

### 6.2.2 Port-Map Verification

Verify every wrapper port maps to the correct underlying macro port:

```python
def verify_port_mapping(mapping_json: Path, wrapper_v: Path):
    """Check that every tile in mapping.json appears in the wrapper."""
    mapping = json.loads(mapping_json.read_text())
    wrapper_src = wrapper_v.read_text()

    for tile in mapping["tiles"]:
        inst_name = tile["instance_name"]
        assert inst_name in wrapper_src, \
            f"Instance {inst_name} in mapping but not in wrapper RTL"
```

### 6.2.3 Bit Mapping Smoke Test

For each tile, verify that data-bit and address ranges are consistent:

```python
def check_bit_continuity(tiles: list[dict]):
    """Ensure no gaps or overlaps in bit assignments."""
    for row in group_by_row(tiles):
        bits_covered = set()
        for tile in row:
            bit_range = range(tile["bit_lo"], tile["bit_hi"])
            overlap = bits_covered & set(bit_range)
            assert not overlap, f"Overlap at bits {overlap}"
            bits_covered.update(bit_range)
    # Total bits must equal exposed width
    assert len(bits_covered) == exposed_width
```

---

## 6.3 Functional Simulation

### 6.3.1 Self-Checking Testbench Strategy

Write a minimal SystemVerilog testbench for each wrapper that:

1. **Writes known patterns** to every address (walking-1, all-0, all-1, random)
2. **Reads back** and checks data matches
3. **Crosses tile boundaries** — specifically test addresses at `child_words - 1`, `child_words`, `child_words + 1`
4. **Tests edge-padded bits** — read bits beyond exposed_width, expect 0

```systemverilog
module wrapper_tb;
  // ...
  initial begin
    // Write phase
    for (int addr = 0; addr < DEPTH; addr++) begin
      write(addr, test_pattern(addr));
    end
    // Read-back phase
    for (int addr = 0; addr < DEPTH; addr++) begin
      read(addr, rdata);
      assert(rdata[WIDTH-1:0] == test_pattern(addr))
        else $error("Mismatch at addr %0d: exp=%h got=%h",
                     addr, test_pattern(addr), rdata[WIDTH-1:0]);
    end
    $display("PASS: %0d addresses verified", DEPTH);
    $finish;
  end
endmodule
```

### 6.3.2 Boundary-Condition Tests

| Test | What it validates |
|------|-------------------|
| Write address 0, read address 0 | Basic connectivity |
| Write last address, read last address | Full address decode |
| Write at tile boundary `child_words - 1` → `child_words` | Row-select transition |
| Read exposed_width bits | No bit truncation |
| Read beyond exposed_width | Edge padding returns 0 |

### 6.3.3 Multi-Port Protocol Tests

For dual-port and 1R1W memories:

- Simultaneous read + write to **same address** → check behavior
- Simultaneous read + write to **different addresses** → both succeed
- Write collision on dual-port → dependent on compiler spec

---

## 6.4 End-to-End Regression

### 6.4.1 Golden Reference Flow

```
For each memory type:
  1. Pick a representative (name, width, depth)
  2. Run full `memgen run` pipeline
  3. Verify:
     a. Compiler output artifacts exist & non-empty
     b. Wrapper RTL passes lint
     c. mapping.json is well-formed
     d. filelist.f lists all required files
  4. (Optional) Run simulation testbench → PASS
```

### 6.4.2 Regression Table

Track test results in a table:

| Memory | Width | Depth | Tiles | Compiler | Lint | Sim | Status |
|--------|-------|-------|-------|----------|------|-----|--------|
| 1prf 8×16 | 40 | 20 | 3H×3V | ✅ | ✅ | ✅ | PASS |
| spsram 256×32 | 256 | 1024 | 1H×1V | ✅ | ✅ | ✅ | PASS |
| dpsram 128×16 | 200 | 64 | 2H×4V | ✅ | ✅ | — | PASS |

### 6.4.3 CI Integration

```yaml
# .github/workflows/validate.yml
name: Memory Generator Validation
on: [push, pull_request]
jobs:
  lint-and-check:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - run: pip install -e .
      - run: memgen families          # Smoke test
      - run: memgen check ts5n12ffcllulvta8x16m1swsho
      - run: memgen plan ts5n12ffcllulvta8x16m1swsho --width 40 --depth 20 --json
      # Full pipeline tests require compiler license — run on self-hosted runner
```

---

## 6.5 Datasheet Cross-Validation

If the memory compiler produces datasheets (.ds files), extract key parameters and cross-check:

```python
def cross_check_datasheet(ds_file: Path, expected_words: int, expected_bits: int):
    """Parse datasheet and verify dimensions match request."""
    ds = parse_datasheet(ds_file)
    assert ds["words"] == expected_words
    assert ds["bits"]  == expected_bits
    # Timing values should be positive and reasonable
    assert 0 < ds["tCK"]  < 100_000  # ps
    assert 0 < ds["tAA"]  < ds["tCK"]
```

This acts as an independent validation that the compiler understood the configuration correctly.

---

## 6.6 Validation Checklist

Before shipping a new memory type or a wrapper update:

- [ ] All supported families listed in `memgen families`
- [ ] Name parser correctly decodes ≥ 5 representative names per family
- [ ] Tiling plan produces correct tile counts for edge cases (1×1, max×max, non-divisible)
- [ ] Compiler invocation succeeds for at least one representative per family
- [ ] Generated RTL passes Verilator lint with zero warnings
- [ ] Port mapping matches `mapping.json`
- [ ] Bit-range coverage is contiguous with no gaps or overlaps
- [ ] Functional simulation passes for at least one tile configuration
- [ ] CI pipeline runs offline checks (families, check, plan) on every push
