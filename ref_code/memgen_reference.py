#!/usr/bin/env python3
"""
Memory Compiler CLI Wrapper (memgen)

A general-purpose CLI that wraps TSMC N12 Memory Compiler family packages
to produce SRAM/register-file artifacts.

Usage:
    memgen families              # List available families
    memgen compile <family> <combo> [--vt <vt>]  # Compile a specific combo
    memgen batch <config_file>   # Batch compile from config
"""

import argparse
import json
import os
import subprocess
import shutil
import sys
import re
from pathlib import Path
from dataclasses import dataclass
from typing import Optional, Dict, List, Tuple
from datetime import datetime


MC2_DIR = Path("/data/eda/tsmc/memory_compiler/tsmc_n12ffcllmc_20131200_100a/MC2_2013.12.00.f")
MC_ROOT = Path("/data/foundry/TSMC12/Memory_compiler")

WORKSPACE_ROOT = Path("/data/yunqi/research/auto_opt/mem_solution/outputs/run_023/gen_001/workspace")


@dataclass
class FamilySpec:
    family_id: str
    description: str
    compiler_dir: Path
    script_name: str
    mco_name: str
    version: str
    config_format: str  # "spsram", "simple", or "dpsram"
    has_vt: bool
    default_combo: str
    default_vt: Optional[str] = None


FAMILIES: Dict[str, FamilySpec] = {
    "spsram": FamilySpec(
        family_id="spsram",
        description="Single-Port SRAM",
        compiler_dir=Path("/data/foundry/TSMC12/Memory_compiler/tsn12ffcllspsram_20131200_130b/0971001_20211221/TSMCHOME/sram/Compiler/tsn12ffcllspsram_20131200_130b"),
        script_name="tsn12ffcllspsram_130b.pl",
        mco_name="tsn12ffcllspsram_20131200_130b.mco",
        version="130b",
        config_format="spsram",
        has_vt=True,
        default_combo="16x8m2swbasodcp",
        default_vt="ulvt",
    ),
    "1prf": FamilySpec(
        family_id="1prf",
        description="1-Port Register File",
        compiler_dir=Path("/data/foundry/TSMC12/Memory_compiler/tsn12ffcll1prf_20131200_130c/0971001_20211221/TSMCHOME/sram/Compiler/tsn12ffcll1prf_20131200_130c"),
        script_name="tsn12ffcll1prf_130c.pl",
        mco_name="tsn12ffcll1prf_20131200_130c.mco",
        version="130c",
        config_format="simple",
        has_vt=False,
        default_combo="8x16m1s",
    ),
    "dpsram": FamilySpec(
        family_id="dpsram",
        description="Dual-Port SRAM",
        compiler_dir=Path("/data/foundry/TSMC12/Memory_compiler/tsn12ffclldpsram_20131200_130c/0971001_20211221/TSMCHOME/sram/Compiler/tsn12ffclldpsram_20131200_130c"),
        script_name="tsn12ffclldpsram_130c.pl",
        mco_name="tsn12ffclldpsram_20131200_130c.mco",
        version="130c",
        config_format="simple",
        has_vt=False,
        default_combo="64x4m4",
    ),
    "2prf": FamilySpec(
        family_id="2prf",
        description="2-Port Register File",
        compiler_dir=Path("/data/foundry/TSMC12/Memory_compiler/tsn12ffcll2prf_20131200_130a/0971001_20211221/TSMCHOME/sram/Compiler/tsn12ffcll2prf_20131200_130a"),
        script_name="tsn12ffcll2prf_130a.pl",
        mco_name="tsn12ffcll2prf_20131200_130a.mco",
        version="130a",
        config_format="simple",
        has_vt=False,
        default_combo="16x16m2",
    ),
}


def parse_combo(combo: str) -> Tuple[int, int, int, str]:
    """
    Parse a combo string like "16x8m2swbasodcp" or "8x16m1s" or "64x4m4"
    Returns: (words, bits, mux, suffix)
    """
    match = re.match(r'^(\d+)x(\d+)m(\d+)(.*)', combo)
    if not match:
        raise ValueError(f"Invalid combo format: {combo}")
    words = int(match.group(1))
    bits = int(match.group(2))
    mux = int(match.group(3))
    suffix = match.group(4)
    return words, bits, mux, suffix


def build_config_line(family: FamilySpec, combo: str, vt: Optional[str] = None) -> str:
    """
    Build config.txt line for a family.
    """
    if family.config_format == "spsram":
        if vt is None:
            vt = family.default_vt or "ulvt"
        return f"{combo}\t{vt}"
    else:
        return combo


def find_output_dir(work_dir: Path) -> Optional[Path]:
    """
    Find the compiler output directory (named after the generated instance).
    """
    for item in work_dir.iterdir():
        if item.is_dir() and not item.name.startswith('.'):
            if (item / "VERILOG").is_dir():
                return item
    return None


def find_verilog_file(output_dir: Path) -> Optional[Path]:
    """
    Find the Verilog model file in the output directory.
    """
    verilog_dir = output_dir / "VERILOG"
    if verilog_dir.is_dir():
        for f in verilog_dir.iterdir():
            if f.suffix == '.v':
                return f
    return None


def find_datasheet(output_dir: Path) -> Optional[Path]:
    """
    Find the datasheet file in the output directory.
    """
    ds_dir = output_dir / "DATASHEET"
    if ds_dir.is_dir():
        for f in ds_dir.iterdir():
            if f.name.startswith("DATASHEET"):
                return f
    for f in output_dir.iterdir():
        if f.name.startswith("DATASHEET") and f.is_file():
            return f
    return None


def generate_wrapper_sv(verilog_path: Path, wrapper_path: Path, family_id: str, combo: str):
    """
    Generate a simple SystemVerilog wrapper for the memory macro.
    """
    verilog_content = verilog_path.read_text()
    
    module_match = re.search(r'^module\s+(\w+)\s*\(([\s\S]*?)\);', verilog_content, re.MULTILINE)
    if not module_match:
        raise ValueError(f"Could not parse module from {verilog_path}")
    
    macro_name = module_match.group(1)
    
    ports = []
    
    input_pattern = re.compile(r'^\s*input\s+(?:wire\s+)?(?:\[(\d+):(\d+)\]\s+)?(\w+);', re.MULTILINE)
    for m in input_pattern.finditer(verilog_content):
        width_high = int(m.group(1) or '0')
        width_low = int(m.group(2) or '0')
        name = m.group(3)
        width = width_high - width_low + 1
        ports.append({'direction': 'input', 'name': name, 'width': width})
    
    output_pattern = re.compile(r'^\s*output\s+(?:wire\s+)?(?:\[(\d+):(\d+)\]\s+)?(\w+);', re.MULTILINE)
    for m in output_pattern.finditer(verilog_content):
        width_high = int(m.group(1) or '0')
        width_low = int(m.group(2) or '0')
        name = m.group(3)
        width = width_high - width_low + 1
        ports.append({'direction': 'output', 'name': name, 'width': width})
    
    words, bits, mux, suffix = parse_combo(combo)
    wrapper_name = f"{family_id}_{combo}_wrapper"
    
    port_decls = []
    for p in ports:
        if p['width'] > 1:
            port_decls.append(f"    {p['direction']} logic [{p['width']-1}:0] {p['name']}")
        else:
            port_decls.append(f"    {p['direction']} logic {p['name']}")
    
    connections = []
    for p in ports:
        connections.append(f"        .{p['name']}({p['name']})")
    
    wrapper_content = f"""// Auto-generated wrapper for {macro_name}
// Generated by memgen on {datetime.now().isoformat()}
// Memory: {family_id}, combo: {combo} ({words}x{bits}, mux={mux})

module {wrapper_name} (
{",\n".join(port_decls)}
);

    // Macro instance
    {macro_name} u_macro (
{",\n".join(connections)}
    );

endmodule
"""
    
    wrapper_path.write_text(wrapper_content)


def compile_memory(family_id: str, combo: str, vt: Optional[str] = None, 
                   work_base: Optional[Path] = None, timeout: int = 420) -> Dict:
    """
    Compile a memory instance and return result info.
    """
    if family_id not in FAMILIES:
        raise ValueError(f"Unknown family: {family_id}")
    
    family = FAMILIES[family_id]
    
    if work_base is None:
        work_base = WORKSPACE_ROOT / "mc_work"
    
    work_dir = work_base / f"{family_id}_{combo.replace('x', '_')}"
    work_dir.mkdir(parents=True, exist_ok=True)
    
    script_path = family.compiler_dir / family.script_name
    mco_path = family.compiler_dir / family.mco_name
    mco_link = work_dir / family.mco_name
    
    if mco_link.exists() or mco_link.is_symlink():
        mco_link.unlink()
    mco_link.symlink_to(mco_path)
    
    config_line = build_config_line(family, combo, vt)
    config_path = work_dir / "config.txt"
    config_path.write_text(config_line + "\n")
    
    run_info = {
        'family': family_id,
        'combo': combo,
        'vt': vt,
        'config': config_line,
        'work_dir': str(work_dir),
        'timestamp': datetime.now().isoformat(),
    }
    (work_dir / "request.json").write_text(json.dumps(run_info, indent=2))
    
    env = os.environ.copy()
    env['MC2_INSTALL_DIR'] = str(MC2_DIR)
    env['MC_HOME'] = str(family.compiler_dir)
    env['PATH'] = str(MC2_DIR / "bin" / "Linux-64") + ":" + env.get('PATH', '')
    
    cmd = [
        'perl', str(script_path),
        '-file', 'config.txt',
        '-VERILOG', '-DATASHEET',
        '-NonBIST', '-NonSLP', '-NonDSLP', '-NonSD',
    ]
    
    log_path = work_dir / "mc.log"
    
    print(f"Compiling {family_id}/{combo}...")
    print(f"  Work dir: {work_dir}")
    print(f"  Config: {config_line}")
    
    result = subprocess.run(
        cmd,
        cwd=str(work_dir),
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    
    log_content = result.stdout + "\n" + result.stderr
    log_path.write_text(log_content)
    
    print(f"  Exit code: {result.returncode}")
    
    output_dir = find_output_dir(work_dir)
    if not output_dir:
        print(f"  ERROR: No output directory found")
        run_info['status'] = 'FAIL'
        run_info['error'] = 'No output directory'
        return run_info
    
    verilog_file = find_verilog_file(output_dir)
    datasheet_file = find_datasheet(output_dir)
    
    if not verilog_file:
        print(f"  ERROR: No Verilog file found")
        run_info['status'] = 'FAIL'
        run_info['error'] = 'No Verilog output'
        return run_info
    
    print(f"  Output: {output_dir.name}")
    print(f"  Verilog: {verilog_file.name}")
    
    artifacts_dir = WORKSPACE_ROOT / "artifacts" / family_id / combo
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    
    verilog_dest = artifacts_dir / f"{output_dir.name}_model.v"
    shutil.copy2(verilog_file, verilog_dest)
    
    if datasheet_file:
        ds_dest = artifacts_dir / f"DATASHEET_{combo}.txt"
        shutil.copy2(datasheet_file, ds_dest)
    
    wrapper_path = artifacts_dir / f"{family_id}_{combo}_wrapper.sv"
    generate_wrapper_sv(verilog_file, wrapper_path, family_id, combo)
    
    run_info['status'] = 'SUCCESS'
    run_info['output_dir'] = str(output_dir)
    run_info['verilog'] = str(verilog_dest)
    run_info['wrapper'] = str(wrapper_path)
    run_info['macro_name'] = output_dir.name
    
    print(f"  Artifacts: {artifacts_dir}")
    
    return run_info


def cmd_families(args):
    """List available families."""
    print("Available Memory Compiler Families:")
    print("-" * 60)
    for fid, spec in FAMILIES.items():
        print(f"  {fid}: {spec.description}")
        print(f"    Version: {spec.version}")
        print(f"    Config format: {spec.config_format}")
        print(f"    Default combo: {spec.default_combo}")
        if spec.has_vt:
            print(f"    VT options: ulvt, lvt, svt")
        print()


def cmd_compile(args):
    """Compile a specific memory combo."""
    family_id = args.family
    combo = args.combo
    vt = args.vt
    
    result = compile_memory(family_id, combo, vt)
    
    if result['status'] == 'SUCCESS':
        print(f"\nSUCCESS: {family_id}/{combo} compiled")
        print(f"  Verilog: {result['verilog']}")
        print(f"  Wrapper: {result['wrapper']}")
    else:
        print(f"\nFAILED: {result['error']}")
        sys.exit(1)


def cmd_batch(args):
    """Batch compile from a config file."""
    config_path = Path(args.config)
    if not config_path.exists():
        print(f"Config file not found: {config_path}")
        sys.exit(1)
    
    configs = []
    for line in config_path.read_text().strip().split('\n'):
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        parts = line.split()
        if len(parts) >= 2:
            configs.append({'family': parts[0], 'combo': parts[1], 'vt': parts[2] if len(parts) > 2 else None})
    
    results = []
    for cfg in configs:
        result = compile_memory(cfg['family'], cfg['combo'], cfg['vt'])
        results.append(result)
    
    success_count = sum(1 for r in results if r['status'] == 'SUCCESS')
    print(f"\nBatch complete: {success_count}/{len(results)} successful")


def cmd_test(args):
    """Run test suite: 2 families x 2 combos each."""
    test_configs = [
        {'family': 'spsram', 'combo': '16x8m2swbasodcp', 'vt': 'ulvt'},
        {'family': 'spsram', 'combo': '64x16m4swbasodcp', 'vt': 'ulvt'},
        {'family': '1prf', 'combo': '8x16m1s', 'vt': None},
        {'family': '1prf', 'combo': '16x32m1s', 'vt': None},
    ]
    
    results = []
    artifacts = []
    
    for cfg in test_configs:
        print(f"\n{'='*60}")
        print(f"Test: {cfg['family']}/{cfg['combo']}")
        print('='*60)
        result = compile_memory(cfg['family'], cfg['combo'], cfg['vt'])
        results.append(result)
        if result['status'] == 'SUCCESS':
            artifacts.append(result['verilog'])
            artifacts.append(result['wrapper'])
    
    success_count = sum(1 for r in results if r['status'] == 'SUCCESS')
    print(f"\n{'='*60}")
    print(f"Test Suite Complete: {success_count}/{len(results)} successful")
    print('='*60)
    
    done_path = WORKSPACE_ROOT / "DONE.txt"
    done_path.write_text("\n".join(artifacts) + "\n")
    print(f"\nDONE.txt written with {len(artifacts)} artifact paths")
    
    return success_count == len(results)


def main():
    parser = argparse.ArgumentParser(description="Memory Compiler CLI Wrapper")
    subparsers = parser.add_subparsers(dest='command', help='Commands')
    
    families_parser = subparsers.add_parser('families', help='List available families')
    families_parser.set_defaults(func=cmd_families)
    
    compile_parser = subparsers.add_parser('compile', help='Compile a memory combo')
    compile_parser.add_argument('family', help='Family ID (spsram, 1prf, dpsram, 2prf)')
    compile_parser.add_argument('combo', help='Combo string (e.g. 16x8m2swbasodcp)')
    compile_parser.add_argument('--vt', default=None, help='VT type (ulvt, lvt, svt)')
    compile_parser.set_defaults(func=cmd_compile)
    
    batch_parser = subparsers.add_parser('batch', help='Batch compile from config file')
    batch_parser.add_argument('config', help='Config file path')
    batch_parser.set_defaults(func=cmd_batch)
    
    test_parser = subparsers.add_parser('test', help='Run test suite')
    test_parser.set_defaults(func=cmd_test)
    
    args = parser.parse_args()
    
    if args.command is None:
        parser.print_help()
        sys.exit(1)
    
    args.func(args)


if __name__ == '__main__':
    main()