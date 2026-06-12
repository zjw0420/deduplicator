"""
Deduplicator — Fast disk duplicate detection without reading entire files.

Algorithm:
  1. Name+Size match (0 I/O) — catches copy/paste duplicates instantly
  2. Size grouping — files with same size but different names are candidates
  3. 3-point sample hash — read 3 × 4KB chunks (head, 1/3, 2/3 position),
     hash them together. ~12KB I/O per file instead of reading the whole thing.

Why 3-point sampling works:
  Two different files producing identical 4KB at three different positions
  is astronomically unlikely. File headers are format-specific (JPEG=FFD8,
  PDF=%PDF, EXE=MZ), so even a single point catches most differences.

Author: DiskDoctor project
License: MIT
"""

import hashlib
import os
from collections import defaultdict
from typing import Optional


def find_duplicates(file_list: list) -> list:
    """
    Find duplicate files in a list of {path, name, size} dicts.

    Returns list of duplicate groups:
    [{"files": ["path1", "path2"], "size": 12345, "wasted": 12345}, ...]
    """
    # Tier 1: Name+Size — instant, zero I/O
    name_map = defaultdict(list)
    size_map = defaultdict(list)
    for f in file_list:
        if f["size"] < 1024:
            continue
        name_map[(f["name"].lower(), f["size"])].append(f)
        size_map[f["size"]].append(f)

    dup_groups = []
    seen = set()

    for (name, sz), group in name_map.items():
        if len(group) >= 2:
            paths = [f["path"] for f in group]
            dup_groups.append({
                "files": paths,
                "size": sz,
                "wasted": sz * (len(paths) - 1),
            })
            seen.update(paths)

    # Tier 2: Same size, different names — 3-point sample hash
    candidates = []
    for sz, group in size_map.items():
        remaining = [f for f in group if f["path"] not in seen]
        if len(remaining) >= 2 and len(remaining) <= 200:
            candidates.append((sz, remaining))

    # Process largest files first (biggest potential waste)
    candidates.sort(key=lambda x: -x[0] * len(x[1]))
    max_samples = 20000

    sampled = 0
    for sz, group in candidates:
        if sampled >= max_samples:
            break
        sig_map = defaultdict(list)
        for f in group:
            if sampled >= max_samples:
                break
            sig = _sample_hash(f["path"], f["size"])
            sampled += 1
            if sig:
                sig_map[sig].append(f)
        for sig, matches in sig_map.items():
            if len(matches) >= 2:
                paths = [f["path"] for f in matches]
                dup_groups.append({
                    "files": paths,
                    "size": sz,
                    "wasted": sz * (len(paths) - 1),
                })

    dup_groups.sort(key=lambda d: -d["wasted"])
    return dup_groups


def _sample_hash(path: str, size: int) -> Optional[str]:
    """
    Read 3 × 4KB chunks from a file and hash them together.

    Reads head, 1/3 position, and 2/3 position.
    Total I/O: ~12KB per file regardless of file size.
    """
    try:
        h = hashlib.md5()
        with open(path, "rb") as f:
            h.update(f.read(4096))  # head
            if size > 8192:
                f.seek(size // 3)
                h.update(f.read(4096))
            if size > 16384:
                f.seek(size * 2 // 3)
                h.update(f.read(4096))
        return h.hexdigest()
    except (OSError, PermissionError):
        return None


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("Usage: python deduplicator.py <directory>")
        sys.exit(1)
    root = sys.argv[1]
    files = []
    for dirpath, _, filenames in os.walk(root):
        for fn in filenames:
            fp = os.path.join(dirpath, fn)
            try:
                st = os.stat(fp)
            except OSError:
                continue
            files.append({"path": fp, "name": fn, "size": st.st_size})
    print(f"Found {len(files)} files, analyzing...")
    dups = find_duplicates(files)
    total_wasted = sum(d["wasted"] for d in dups)
    print(f"{len(dups)} duplicate groups found, {total_wasted / (1024**3):.1f} GB wasted")
    for d in dups[:10]:
        names = ", ".join(os.path.basename(p) for p in d["files"][:3])
        print(f"  {names} — {len(d['files'])} copies, {d['wasted']/(1024**2):.0f} MB wasted")
