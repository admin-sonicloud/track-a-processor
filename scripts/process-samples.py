#!/usr/bin/env python3
"""
Process a publicsamples SFZ library: download → extract → transcode → push.

Designed to run on GitHub Actions (14GB disk, good network).
Processes one category at a time to fit within disk limits.
"""
import argparse, json, os, re, shutil, subprocess, sys, time, urllib.request, urllib.parse, zipfile, tempfile
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

OWNER = "zulfikarbarbora-outl"
PUBSAMPLES_OWNER = "publicsamples"
WORK = Path("/tmp/process-samples")
OUTPUT = Path("output")

def gh_api(url, token=None, method="GET", data=None):
    """GitHub API call."""
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "process-samples/1.0"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if data:
        headers["Content-Type"] = "application/json"
        body = json.dumps(data).encode()
    else:
        body = None
    req = urllib.request.Request(url, method=method, headers=headers, data=body)
    with urllib.request.urlopen(req, timeout=30) as r:
        content = r.read()
        return json.loads(content) if content else {}

def download_asset(url, dest, token=None, timeout=6000):
    """Download a release asset."""
    headers = {"User-Agent": "process-samples/1.0", "Accept": "application/octet-stream"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        with open(dest, "wb") as f:
            while True:
                chunk = r.read(65536)
                if not chunk:
                    break
                f.write(chunk)
    return dest.exists() and dest.stat().st_size > 0

def transcode_to_opus(wav_path, opus_path):
    """Transcode WAV/AIF → Opus 48k."""
    r = subprocess.run(
        ["ffmpeg", "-y", "-i", str(wav_path), "-c:a", "libopus", "-b:a", "48k", "-ar", "48000", "-vn", str(opus_path)],
        capture_output=True, timeout=600
    )
    return r.returncode == 0 and opus_path.exists() and opus_path.stat().st_size > 200

def push_repo(repo_name, push_dir, commit_msg, token):
    """Push files to a GitHub repo using git."""
    GH_TOKEN = token
    # Clone the repo (or init if empty)
    clone_dir = WORK / f"{repo_name}-clone"
    if clone_dir.exists():
        shutil.rmtree(clone_dir)
    
    repo_url = f"https://{GH_TOKEN}@github.com/{OWNER}/{repo_name}.git"
    
    # Try cloning (may fail if empty)
    r = subprocess.run(["git", "clone", "--depth", "1", repo_url, str(clone_dir)], capture_output=True, timeout=600)
    if r.returncode != 0:
        # Repo is empty — init locally
        clone_dir.mkdir(parents=True)
        subprocess.run(["git", "init"], cwd=clone_dir, capture_output=True)
        subprocess.run(["git", "checkout", "-b", "main"], cwd=clone_dir, capture_output=True)
    
    subprocess.run(["git", "config", "user.name", "zmmac1"], cwd=clone_dir, capture_output=True)
    subprocess.run(["git", "config", "user.email", "zmmac1@users.noreply.github.com"], cwd=clone_dir, capture_output=True)
    
    # Copy files
    for f in push_dir.iterdir():
        if f.is_file():
            shutil.copy2(f, clone_dir / f.name)
    
    # Commit + push
    subprocess.run(["git", "add", "-A"], cwd=clone_dir, capture_output=True)
    r = subprocess.run(["git", "commit", "-m", commit_msg], cwd=clone_dir, capture_output=True, text=True)
    if r.returncode != 0 and "nothing to commit" not in r.stdout:
        print(f"  commit failed: {r.stderr[:200]}", flush=True)
        return False
    
    # Push
    for attempt in range(3):
        r = subprocess.run(["git", "push", repo_url, "main"], cwd=clone_dir, capture_output=True, text=True, timeout=600)
        if r.returncode == 0:
            return True
        print(f"  push attempt {attempt+1} failed: {r.stderr[:200]}", flush=True)
        time.sleep(5)
    return False

def update_master_db(lib_id, lib_name, instruments, zones, opus_repo, token):
    """Update master-db.json in web-daw-samples."""
    clone_dir = WORK / "web-daw-samples-clone"
    if clone_dir.exists():
        shutil.rmtree(clone_dir)
    
    repo_url = f"https://{token}@github.com/{OWNER}/web-daw-samples.git"
    subprocess.run(["git", "clone", "--depth", "1", repo_url, str(clone_dir)], capture_output=True, timeout=600)
    subprocess.run(["git", "config", "user.name", "zmmac1"], cwd=clone_dir, capture_output=True)
    subprocess.run(["git", "config", "user.email", "zmmac1@users.noreply.github.com"], cwd=clone_dir, capture_output=True)
    
    # Read master-db
    db_path = clone_dir / "sldf" / "master-db.json"
    db = json.loads(db_path.read_text())
    
    # Remove existing entry if present
    db["libraries"] = [l for l in db["libraries"] if l["id"] != f"{lib_id}.sldf"]
    
    # Add new entry
    opus_base_url = f"https://raw.githubusercontent.com/{OWNER}/{opus_repo}/main/"
    db["libraries"].append({
        "id": f"{lib_id}.sldf",
        "name": lib_name,
        "description": f"Vintage synth SFZ from publicsamples (CC0)",
        "author": "publicsamples",
        "license": "CC0-1.0",
        "instrumentCount": instruments,
        "zoneCount": zones,
        "sldfUrl": f"https://raw.githubusercontent.com/{OWNER}/web-daw-samples/main/sldf/{lib_id}.sldf.v3.json",
        "losslessRepo": lib_id,
        "losslessUrl": f"https://github.com/{PUBSAMPLES_OWNER}/{lib_id}",
        "losslessBaseUrl": f"https://github.com/{PUBSAMPLES_OWNER}/{lib_id}/releases",
        "opusRepo": opus_repo,
        "opusRepoUrl": f"https://github.com/{OWNER}/{opus_repo}",
        "opusBaseUrl": opus_base_url,
        "opusIndexUrl": f"{opus_base_url}index.json",
        "opusReadmeUrl": f"{opus_base_url}README.md",
    })
    
    # Recompute totals
    db["totals"] = {
        "libraries": len(db["libraries"]),
        "instruments": sum(l.get("instrumentCount", 0) for l in db["libraries"]),
        "zones": sum(l.get("zoneCount", 0) for l in db["libraries"]),
    }
    
    db_path.write_text(json.dumps(db, indent=2))
    
    # Commit + push
    subprocess.run(["git", "add", "sldf/master-db.json"], cwd=clone_dir, capture_output=True)
    subprocess.run(["git", "commit", "-m", f"feat: add {lib_id} ({instruments} inst, {zones} zones)"], cwd=clone_dir, capture_output=True, text=True)
    
    for attempt in range(3):
        r = subprocess.run(["git", "push", repo_url, "main"], cwd=clone_dir, capture_output=True, text=True, timeout=600)
        if r.returncode == 0:
            print(f"  Updated master-db.json", flush=True)
            return True
        time.sleep(5)
    return False

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-name", required=True, help="publicsamples repo name")
    parser.add_argument("--lib-id", required=True, help="Library ID")
    parser.add_argument("--opus-repo", required=True, help="Opus repo name")
    parser.add_argument("--categories", default="all", help="Categories to process (comma-separated, or 'all')")
    args = parser.parse_args()
    
    GH_TOKEN = os.environ.get("GH_TOKEN", "")
    if not GH_TOKEN:
        print("ERROR: GH_TOKEN not set", file=sys.stderr)
        return 1
    
    WORK.mkdir(parents=True, exist_ok=True)
    OUTPUT.mkdir(exist_ok=True)
    
    print(f"=== Processing {args.repo_name} → {args.lib_id} ===", flush=True)
    
    # Get release assets
    url = f"https://api.github.com/repos/{PUBSAMPLES_OWNER}/{args.repo_name}/releases"
    releases = gh_api(url, token=GH_TOKEN)
    if not releases:
        print(f"No releases found for {args.repo_name}", flush=True)
        return 1
    
    rel = releases[0]
    assets = rel.get("assets", [])
    print(f"Release: {rel.get('tag_name')}, {len(assets)} assets", flush=True)
    
    # Group by category
    from collections import defaultdict
    categories = defaultdict(list)
    for a in assets:
        cat = a["name"].split(".")[0]
        categories[cat].append(a)
    
    if args.categories != "all":
        wanted = set(args.categories.split(","))
        categories = {k: v for k, v in categories.items() if k in wanted}
    
    print(f"Categories: {sorted(categories.keys())}", flush=True)
    
    # Clone the publicsamples repo (SFZ files only — no audio committed)
    clone_dir = WORK / "sfz-repo"
    if clone_dir.exists():
        shutil.rmtree(clone_dir)
    clone_url = f"https://github.com/{PUBSAMPLES_OWNER}/{args.repo_name}.git"
    subprocess.run(["git", "clone", "--depth", "1", clone_url, str(clone_dir)], capture_output=True, timeout=120)
    sfz_files = sorted(clone_dir.rglob("*.sfz"))
    print(f"Found {len(sfz_files)} SFZ files", flush=True)
    
    # Process each category
    opus_dir = WORK / "opus"
    if opus_dir.exists():
        shutil.rmtree(opus_dir)
    opus_dir.mkdir()
    
    total_transcoded = 0
    for cat, cat_assets in sorted(categories.items()):
        cat_size = sum(a["size"] for a in cat_assets)
        print(f"\n--- Category: {cat} ({cat_size/1024/1024:.1f} MB) ---", flush=True)
        
        # Download assets
        cat_dir = WORK / f"cat-{cat}"
        if cat_dir.exists():
            shutil.rmtree(cat_dir)
        cat_dir.mkdir()
        
        parts = []
        for asset in sorted(cat_assets, key=lambda a: a["name"]):
            dest = cat_dir / asset["name"]
            print(f"  Downloading {asset['name']} ({asset['size']/1024/1024:.1f} MB)...", flush=True)
            if download_asset(asset["browser_download_url"], dest, token=GH_TOKEN, timeout=6000):
                parts.append(dest)
        
        if not parts:
            print(f"  No assets downloaded!", flush=True)
            continue
        
        # Extract (split zip: combine parts then extract)
        extract_dir = cat_dir / "extracted"
        extract_dir.mkdir()
        if len(parts) == 1 and parts[0].suffix == ".zip":
            try:
                with zipfile.ZipFile(parts[0]) as z:
                    z.extractall(extract_dir)
            except Exception as e:
                print(f"  Extract failed: {e}", flush=True)
                continue
        else:
            combined = cat_dir / f"{cat}.zip"
            with open(combined, "wb") as out:
                for part in parts:
                    with open(part, "rb") as f:
                        shutil.copyfileobj(f, out)
            try:
                with zipfile.ZipFile(combined) as z:
                    z.extractall(extract_dir)
            except Exception as e:
                print(f"  Split zip extract failed: {e}", flush=True)
                continue
            finally:
                combined.unlink(missing_ok=True)
        
        # Delete downloaded parts to save disk
        for part in parts:
            part.unlink(missing_ok=True)
        
        # Find audio files + transcode to Opus
        audio_files = []
        for ext in ["*.wav", "*.aif", "*.aiff", "*.flac"]:
            audio_files.extend(extract_dir.rglob(ext))
        
        print(f"  {len(audio_files)} audio files to transcode", flush=True)
        
        for i, wav in enumerate(audio_files, 1):
            opus_name = wav.stem + ".opus"
            # Sanitize: replace spaces/special chars
            opus_name = re.sub(r"[^A-Za-z0-9._-]", "_", opus_name)
            opus_path = opus_dir / f"{cat}_{opus_name}"
            if transcode_to_opus(wav, opus_path):
                total_transcoded += 1
            # Delete WAV to save disk
            wav.unlink(missing_ok=True)
            if i % 100 == 0:
                print(f"    {i}/{len(audio_files)} transcoded", flush=True)
        
        # Clean up extracted dir
        shutil.rmtree(extract_dir, ignore_errors=True)
        
        # Check disk space
        disk_free = shutil.disk_usage("/").free / (1024**3)
        print(f"  Disk free: {disk_free:.1f} GB, total opus: {total_transcoded}", flush=True)
    
    print(f"\n=== Total opus files: {total_transcoded} ===", flush=True)
    
    if total_transcoded == 0:
        print("No opus files produced!", flush=True)
        return 1
    
    # Push opus to repo
    print(f"\nPushing {total_transcoded} opus files to {args.opus_repo}...", flush=True)
    
    # Create index.json + README
    opus_files = list(opus_dir.iterdir())
    total_size = sum(f.stat().st_size for f in opus_files)
    (opus_dir / "index.json").write_text(json.dumps({
        "library": args.lib_id, "format": "opus", "fileCount": len(opus_files),
        "totalSizeBytes": total_size, "generatedAt": "2026-08-17",
    }, indent=2) + "\n")
    (opus_dir / "README.md").write_text(f"# {args.lib_id} (Opus 48k)\n\n- **Files**: {len(opus_files)}\n- **Source**: github.com/{PUBSAMPLES_OWNER}/{args.repo_name}\n- **License**: CC0-1.0\n")
    
    # Push via git (GHA has good connectivity)
    ok = push_repo(args.opus_repo, opus_dir, f"feat: {args.lib_id} opus previews ({total_transcoded} files, {total_size/1024/1024:.1f} MB)", GH_TOKEN)
    if not ok:
        print("Push failed!", flush=True)
        return 1
    
    # Count instruments + zones from SFZ
    instruments = len(sfz_files)
    zones = total_transcoded  # approximate
    
    # Update master-db
    lib_name = args.repo_name.replace("-", " ")
    update_master_db(args.lib_id, lib_name, instruments, zones, args.opus_repo, GH_TOKEN)
    
    print(f"\n=== DONE: {args.lib_id} ({instruments} inst, {zones} zones, {total_transcoded} opus) ===", flush=True)
    
    # Save results
    (OUTPUT / "results.json").write_text(json.dumps({
        "lib_id": args.lib_id,
        "instruments": instruments,
        "zones": zones,
        "opus_files": total_transcoded,
        "opus_size_mb": total_size / (1024 * 1024),
    }, indent=2))
    
    return 0

if __name__ == "__main__":
    sys.exit(main())
