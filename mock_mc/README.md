# Mock Memory Compiler

This is a **mock memory compiler** for offline evaluation / CI environments
where no real foundry EDA tools are available.

## Invocation

```bash
# Option 1: direct Python
python compile.py --name <memory_name> --outdir <output_dir>

# Option 2: shell wrapper
./mc.sh <memory_name> <output_dir>

# Option 3: with explicit kits
python compile.py --name ts5n12ffcllspsram_ulvta8x256m4 \
                  --outdir /tmp/output \
                  --kits LEF LIB GDS VERILOG
```

## Output structure

Given `--name <N>` and `--outdir <D>`, the compiler creates:

```
<D>/
  <N>/
    VERILOG/<N>.v
    LEF/<N>.lef
    LIB/<N>.lib
    GDS/<N>.gds
```

All output files are **stub/placeholder** files containing minimal valid headers.
Exit code is always 0 (success).

## Config-file mode

Also accepts a `-file <config.txt>` flag to mimic TSMC perl script convention:

```bash
./mc.sh -file config.txt -DATASHEET
```

The config file should contain lines like `NAME=<mem_name>` and `OUTDIR=<dir>`.
If these keys are absent, defaults are used.
