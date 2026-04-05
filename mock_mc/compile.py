#!/usr/bin/env python3
"""
mock_mc/compile.py  –  Mock Memory Compiler
============================================
Simulates a foundry memory compiler.  Produces stub output files so that
downstream RTL-wrapper tooling (wrapper.py / generate.py) can complete without
a real EDA license.

CLI (two modes):
  1. Keyword-arg mode:
       python compile.py --name <MEM_NAME> --outdir <DIR> [--kits LEF LIB GDS VERILOG]

  2. Config-file mode (TSMC Perl convention):
       python compile.py -file config.txt [-DATASHEET]
       The config.txt must contain at least:
         NAME=<mem_name>
         OUTDIR=<outdir>       (optional, defaults to CWD/<mem_name>)
"""

import argparse
import os
import sys
import textwrap
from pathlib import Path

# ---------------------------------------------------------------------------
# Stub file content generators
# ---------------------------------------------------------------------------

def _verilog_stub(name: str) -> str:
    return textwrap.dedent(f"""\
        // *** MOCK COMPILER OUTPUT ***  name={name}
        `timescale 1ns / 1ps
        module {name} (
          input        CLK,
          input        CEB,
          input        WEB,
          input  [7:0] A,
          input  [31:0] D,
          output [31:0] Q
        );
          // Stub behaviour model
          reg [31:0] mem [0:255];
          reg [31:0] Q_reg;
          always @(posedge CLK) begin
            if (!CEB) begin
              if (!WEB) mem[A] <= D;
              else      Q_reg  <= mem[A];
            end
          end
          assign Q = Q_reg;
        endmodule
        """)


def _lib_stub(name: str) -> str:
    """Minimal Liberty (.lib) stub with correct header (>1 kB when padded)."""
    header = textwrap.dedent(f"""\
        /* *** MOCK COMPILER OUTPUT ***  name={name} */
        library ({name}) {{
          technology (cmos) ;
          delay_model : table_lookup ;
          time_unit : "1ns" ;
          voltage_unit : "1V" ;
          current_unit : "1mA" ;
          pulling_resistance_unit : "1kohm" ;
          leakage_power_unit : "1mW" ;
          capacitive_load_unit(1, pf) ;

          cell ({name}) {{
            area : 5000 ;
            pin (CLK)  {{ direction : input; clock : true; }}
            pin (CEB)  {{ direction : input; }}
            pin (WEB)  {{ direction : input; }}
            pin (A[7:0]) {{ direction : input; }}
            pin (D[31:0]) {{ direction : input; }}
            pin (Q[31:0]) {{ direction : output; function : "mem_Q"; }}
          }}
        }}  /* end library */
        """)
    # pad to well above 1000 bytes
    padding = "/* " + "x" * 60 + " */\n"
    while len(header) < 1100:
        header += padding
    return header


def _lef_stub(name: str) -> str:
    return textwrap.dedent(f"""\
        # *** MOCK COMPILER OUTPUT ***  name={name}
        VERSION 5.7 ;
        NAMESCASESENSITIVE ON ;
        MACRO {name}
          CLASS BLOCK ;
          ORIGIN 0.000 0.000 ;
          FOREIGN {name} 0.000 0.000 ;
          SIZE 200.000 BY 100.000 ;
          PIN CLK
            DIRECTION INPUT ;
            USE SIGNAL ;
          END CLK
        END {name}
        END LIBRARY
        """)


def _gds_stub(name: str) -> bytes:
    """Tiny GDSII-like binary header stub (>100 bytes)."""
    header = f"MOCK_GDS name={name} ".encode() + b"\x00" * 100
    return header


# ---------------------------------------------------------------------------
# Config-file parser (TSMC Perl convention)
# ---------------------------------------------------------------------------

def _parse_config_file(path: str) -> dict:
    """Parse KEY=VALUE lines from a config.txt file."""
    result = {}
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                k, _, v = line.partition("=")
                result[k.strip().upper()] = v.strip()
    return result


# ---------------------------------------------------------------------------
# Core compilation logic
# ---------------------------------------------------------------------------

def compile_memory(name: str, outdir: str, kits: list[str] | None = None) -> int:
    """Create stub output files for the requested memory and kits."""
    if kits is None:
        kits = ["VERILOG", "LEF", "LIB", "GDS"]

    mem_dir = Path(outdir) / name

    kits_upper = [k.upper() for k in kits]

    if "VERILOG" in kits_upper:
        vdir = mem_dir / "VERILOG"
        vdir.mkdir(parents=True, exist_ok=True)
        (vdir / f"{name}.v").write_text(_verilog_stub(name), encoding="utf-8")
        print(f"[mock_mc] wrote VERILOG/{name}.v")

    if "LEF" in kits_upper:
        ldir = mem_dir / "LEF"
        ldir.mkdir(parents=True, exist_ok=True)
        (ldir / f"{name}.lef").write_text(_lef_stub(name), encoding="utf-8")
        print(f"[mock_mc] wrote LEF/{name}.lef")

    if "LIB" in kits_upper:
        ldir = mem_dir / "LIB"
        ldir.mkdir(parents=True, exist_ok=True)
        (ldir / f"{name}.lib").write_text(_lib_stub(name), encoding="utf-8")
        print(f"[mock_mc] wrote LIB/{name}.lib")

    if "GDS" in kits_upper:
        gdir = mem_dir / "GDS"
        gdir.mkdir(parents=True, exist_ok=True)
        (gdir / f"{name}.gds").write_bytes(_gds_stub(name))
        print(f"[mock_mc] wrote GDS/{name}.gds")

    print(f"[mock_mc] compilation complete: {mem_dir}")
    return 0


# ---------------------------------------------------------------------------
# Argument parsing — support both modes
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="compile.py",
        description="Mock Memory Compiler — produces stub output files for testing.",
    )
    # Config-file mode (TSMC Perl `-file` convention)
    p.add_argument("-file", dest="config_file", metavar="CONFIG",
                   help="Config file (KEY=VALUE lines).  NAME and OUTDIR are read from it.")
    p.add_argument("-DATASHEET", action="store_true",
                   help="Legacy TSMC flag — accepted but ignored.")

    # Keyword-arg mode
    p.add_argument("--name", help="Memory instance name (canonical form).")
    p.add_argument("--outdir", default=".", help="Base output directory (default: CWD).")
    p.add_argument("--kits", nargs="*",
                   choices=["LEF", "LIB", "GDS", "VERILOG"],
                   default=["LEF", "LIB", "GDS", "VERILOG"],
                   help="Which output kits to generate (default: all).")
    # Positional convenience: compile.py <name> [<outdir>]
    p.add_argument("positional", nargs="*", metavar="ARG",
                   help="Positional: [<name> [<outdir>]]")
    return p


def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()

    name: str | None = None
    outdir: str = "."
    kits = args.kits or ["LEF", "LIB", "GDS", "VERILOG"]

    # ── Config-file mode ──────────────────────────────────────────────────
    if args.config_file:
        cfg = _parse_config_file(args.config_file)
        name   = cfg.get("NAME") or name
        outdir = cfg.get("OUTDIR") or outdir
        if not name:
            print("[mock_mc] WARNING: config file has no NAME= entry; using 'mock_memory'",
                  file=sys.stderr)
            name = "mock_memory"
        print(f"[mock_mc] config-file mode: NAME={name}  OUTDIR={outdir}")
        return compile_memory(name, outdir, kits)

    # ── Keyword-arg / positional mode ────────────────────────────────────
    if args.name:
        name = args.name
    elif args.positional:
        name   = args.positional[0]
        outdir = args.positional[1] if len(args.positional) > 1 else outdir

    if not name:
        print("[mock_mc] ERROR: no memory name provided.  Use --name or positional arg.",
              file=sys.stderr)
        parser.print_help(sys.stderr)
        return 1

    outdir = args.outdir if args.outdir != "." else outdir
    return compile_memory(name, outdir, kits)


if __name__ == "__main__":
    sys.exit(main())
