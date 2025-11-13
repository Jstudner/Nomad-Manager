#!/usr/bin/env python3
"""
nomad_transcode_with_posters.py

My Transcoder system, combined with TMDb poster fetcher designed by RakaMaru (https://github.com/RakaMaru/jcorp-nomad/tree/main)


Key behaviors:
 - Prompts each run for Downloads and Output unless --downloads/--output provided.
 - Default: run until stopped (polling).
 - Supports per-file confirm mode (--confirm), posters-only (--posters-only),
   disable posters (--no-posters), dry-run, reset DB, and one-shot (--one-shot).
 - Posters-only saves posters next to top-level folders in the start location (Downloads root).

Requirements:
 - HandBrakeCLI (handbrake-cli) and ffprobe (ffmpeg) on PATH
 - Python deps: pillow, requests
 - TMDB API key
"""
import os, sys, time, json, shutil, sqlite3, subprocess
from pathlib import Path
from argparse import ArgumentParser
from typing import Optional, Dict, Any, List, Set

# ---------- CONFIG ----------
DB_PATH = Path.home() / ".nomad_transcoder_state.db"
TEMP_SUFFIX = ".transcoding"
VIDEO_EXTS = {".mp4", ".mkv", ".avi", ".m4v", ".ts", ".flv", ".mov"}
TEMP_PATTERNS = [".part", ".crdownload", ".!qB", ".partial", ".downloading"]
SIZE_STABLE_SECONDS = 30
DEFAULT_POLL_INTERVAL = 20
HANDBRAKE_PRESET = "Fast 480p30"

DEFAULT_DOWNLOADS = str(Path.home() / "Transcoding System" / "Input - Downloads")
DEFAULT_OUTPUT = str(Path.home() / "Transcoding System" / "Output")

TMDB_BASE = "https://api.themoviedb.org/3"
TMDB_IMG_BASE = "https://image.tmdb.org/t/p"

# ---------- GLOBAL ARGS (populated later) ----------
ARGS = None

# ---------- UTIL ----------
def log(msg: str):
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}", flush=True)

def run_cmd(cmd: List[str], capture: bool=False):
    if capture:
        return subprocess.run(cmd, check=False, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    return subprocess.run(cmd, check=False)

def ensure_dir(p: Path):
    p.mkdir(parents=True, exist_ok=True)

def unique_dest(dest: Path) -> Path:
    if not dest.exists(): return dest
    base = dest.stem; suf = dest.suffix; i = 1
    while True:
        cand = dest.with_name(f"{base}_{i}{suf}")
        if not cand.exists(): return cand
        i += 1

def is_temporary_name(name: str) -> bool:
    for p in TEMP_PATTERNS:
        if name.endswith(p) or p in name:
            return True
    return False

# ---------- DB ----------
def init_db():
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    cur.execute("""CREATE TABLE IF NOT EXISTS files (path TEXT PRIMARY KEY, status TEXT, added_at INTEGER, updated_at INTEGER, note TEXT)""")
    con.commit()
    return con

def reset_db():
    try:
        DB_PATH.unlink()
        log("Deleted DB for reset.")
    except FileNotFoundError:
        pass

def mark(con, path: Path, status: str, note: Optional[str]=None):
    ts = int(time.time())
    cur = con.cursor()
    cur.execute("""INSERT INTO files(path,status,added_at,updated_at,note) VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(path) DO UPDATE SET status=excluded.status, updated_at=excluded.updated_at, note=excluded.note""",
                (str(path), status, ts, ts, note))
    con.commit()

def status_of(con, path: Path) -> Optional[str]:
    cur = con.cursor()
    cur.execute("SELECT status FROM files WHERE path=?", (str(path),))
    r = cur.fetchone()
    return r[0] if r else None

# ---------- probing & transcode ----------
def file_is_stable(p: Path, wait: int = SIZE_STABLE_SECONDS) -> bool:
    try:
        s1 = p.stat().st_size
    except FileNotFoundError:
        return False
    time.sleep(wait)
    try:
        s2 = p.stat().st_size
    except FileNotFoundError:
        return False
    return s1 == s2 and s1 > 0

def probe_video(path: Path) -> Optional[Dict[str,Any]]:
    cmd = ["ffprobe","-v","error","-select_streams","v:0","-show_entries","stream=codec_name,width,height,bit_rate","-show_entries","format=format_name,duration,size","-of","json", str(path)]
    p = run_cmd(cmd, capture=True)
    if p.returncode != 0 or not p.stdout: return None
    try:
        data = json.loads(p.stdout)
        stream = (data.get("streams") or [{}])[0]
        fmt = data.get("format") or {}
        return {"vcodec": stream.get("codec_name"), "width": int(stream.get("width") or 0), "height": int(stream.get("height") or 0), "format_name": fmt.get("format_name"), "size": int(fmt.get("size") or 0)}
    except Exception:
        return None

def should_skip_by_probe(info: Optional[Dict[str,Any]]) -> bool:
    if not info: return False
    fmt = (info.get("format_name") or "").lower(); v = (info.get("vcodec") or "").lower(); w = info.get("width") or 0
    return ("mp4" in fmt) and (v in ("h264","avc1")) and (w <= 854)

def transcode_with_handbrake(src: Path, dest_tmp: Path) -> int:
    cmd = ["HandBrakeCLI","-i",str(src),"-o",str(dest_tmp),"--preset", HANDBRAKE_PRESET, "-O"]
    if ARGS.dry_run:
        log(f"[DRY-RUN] Would run: {' '.join(cmd)}")
        return 0
    log(f"HandBrakeCLI -> {src.name}")
    return run_cmd(cmd).returncode

def move_safe(src: Path, dest_dir: Path) -> Path:
    ensure_dir(dest_dir)
    dest = dest_dir / src.name
    dest = unique_dest(dest)
    if ARGS.dry_run:
        log(f"[DRY-RUN] Would move {src} -> {dest}")
        return dest
    try:
        src.rename(dest)
    except Exception:
        shutil.copy2(src, dest)
        try:
            src.unlink()
        except Exception:
            pass
    return dest

def copy_tree_safe(src_dir: Path, dest_dir: Path):
    ensure_dir(dest_dir)
    if ARGS.dry_run:
        log(f"[DRY-RUN] Would copy tree {src_dir} -> {dest_dir}")
        return
    shutil.copytree(src_dir, dest_dir, dirs_exist_ok=True)

# ---------- TMDb poster ----------
def load_tmdb_key(script_dir: Path) -> Optional[str]:
    cfg = script_dir / "make_posters.json"
    if not cfg.exists(): return None
    try:
        return json.loads(cfg.read_text(encoding="utf-8")).get("TMDB_API_KEY")
    except Exception:
        return None

def tmdb_search_tv(api_key: str, title: str, lang: str="en-US"):
    import requests
    params = {"api_key": api_key, "query": title, "language": lang, "include_adult":"false"}
    r = requests.get(f"{TMDB_BASE}/search/tv", params=params, timeout=20); r.raise_for_status()
    data = r.json(); results = data.get("results") or []
    return results[0] if results else None

def tmdb_fetch_poster_bytes(api_key: str, poster_path: str):
    import requests
    url = f"{TMDB_IMG_BASE}/original{poster_path}"
    r = requests.get(url, timeout=30)
    return r.content if r.status_code == 200 else None

def fetch_and_save_show_poster(show_name: str, out_root: Path, api_key: Optional[str], downloads_root: Path, posters_only: bool=False) -> bool:
    """
    Save poster as out_root / f"{show_name}.jpg" (top-level in Output root),
    or when posters_only mode is active save into downloads_root (start folder).
    """
    if not api_key:
        log(f"TMDb key not found; skipping poster for '{show_name}'")
        return False
    try:
        found = tmdb_search_tv(api_key, show_name)
        if not found or not found.get("poster_path"):
            log(f"No TMDb match for '{show_name}'")
            return False
        poster = tmdb_fetch_poster_bytes(api_key, found["poster_path"])
        if not poster:
            log(f"No poster bytes for '{show_name}'")
            return False
        if posters_only:
            dest = downloads_root / f"{show_name}.jpg"
        else:
            dest = out_root / f"{show_name}.jpg"
        if ARGS.dry_run:
            log(f"[DRY-RUN] Would write poster {dest}")
            return True
        ensure_dir(dest.parent)
        with open(dest, "wb") as fh:
            fh.write(poster)
        log(f"Saved poster for '{show_name}' -> {dest}")
        return True
    except Exception as e:
        log(f"Poster fetch error for '{show_name}': {e}")
        return False

# ---------- multi-depth collector ----------
def collect_videos_two_depth(top: Path) -> Dict[str, List[Path]]:
    out = {}
    direct = [p for p in top.iterdir() if p.is_file() and p.suffix.lower() in VIDEO_EXTS]
    if direct: out[""] = direct
    for d in [p for p in top.iterdir() if p.is_dir()]:
        vids = []
        # files directly in season dir
        for f in d.iterdir():
            if f.is_file() and f.suffix.lower() in VIDEO_EXTS:
                vids.append(f)
            elif f.is_dir():
                for nested in f.rglob("*"):
                    if nested.is_file() and nested.suffix.lower() in VIDEO_EXTS:
                        vids.append(nested)
        if vids:
            out[d.name] = vids
    return out

# ---------- file processing ----------
def rel_output_path(downloads_root: Path, src: Path, output_root: Path) -> Path:
    rel = src.relative_to(downloads_root)
    dest = output_root / rel
    ensure_dir(dest.parent)
    return dest

def ask_confirm(prompt: str) -> bool:
    if ARGS.dry_run:
        log(f"[DRY-RUN] would ask: {prompt} -> auto-YES")
        return True
    while True:
        ans = input(f"{prompt} [y/N]: ").strip().lower()
        if ans in ("y","yes"): return True
        if ans in ("n","no",""): return False

def process_movie_file(con, src: Path, downloads_root: Path, output_root: Path):
    s = status_of(con, src)
    if s in ("processing","done_moved","skipped_moved","kept_original_moved","copied_season"):
        return
    if is_temporary_name(src.name):
        log(f"Skipping temp file {src.name}")
        return
    if ARGS.confirm and not ask_confirm(f"Process file: {src}?"):
        mark(con, src, "skipped_by_user", "user skipped")
        log(f"User skipped {src.name}")
        return
    log(f"Processing file: {src}")
    if not file_is_stable(src):
        log(f"File not stable yet: {src}")
        return
    info = probe_video(src)
    if should_skip_by_probe(info):
        # copy original to output root preserving relative path
        dest_dir = (output_root / src.relative_to(downloads_root)).parent
        moved = move_safe(src, dest_dir)
        mark(con, src, "skipped_moved", f"moved to {moved}")
        log(f"SKIPPED BY PROBE -> moved original to {moved}")
        return
    # transcode
    mark(con, src, "queued", "ready")
    dest_tmp = (output_root / src.relative_to(downloads_root)).with_suffix(".mp4" + TEMP_SUFFIX)
    ensure_dir(dest_tmp.parent)
    rc = transcode_with_handbrake(src, dest_tmp)
    if rc != 0:
        mark(con, src, "error", f"handbrake_exit_{rc}")
        log(f"HandBrake failed for {src.name}")
        if dest_tmp.exists():
            try: dest_tmp.unlink()
            except: pass
        return
    if not dest_tmp.exists():
        mark(con, src, "error", "no_output")
        log(f"No transcode output for {src.name}")
        return
    try:
        orig_size = src.stat().st_size; new_size = dest_tmp.stat().st_size
    except Exception:
        mark(con, src, "error", "stat_failed"); return
    if orig_size <= new_size:
        # remove transcode tmp, move original to output
        if not ARGS.dry_run:
            try: dest_tmp.unlink()
            except: pass
        dest_dir = (output_root / src.relative_to(downloads_root)).parent
        moved = move_safe(src, dest_dir)
        mark(con, src, "kept_original_moved", f"moved original to {moved}")
        log(f"Kept ORIGINAL (smaller/equal) -> moved to {moved}")
        return
    else:
        dest_final = (output_root / src.relative_to(downloads_root)).with_suffix(".mp4")
        if dest_final.exists(): dest_final = unique_dest(dest_final)
        try:
            if not ARGS.dry_run:
                dest_tmp.rename(dest_final)
                try: src.unlink()
                except: pass
            mark(con, src, "done_moved", f"moved transcode to {dest_final}")
            log(f"Transcode COMPLETE -> {dest_final}")
            return
        except Exception as e:
            mark(con, src, "error", f"move_failed:{e}"); log(f"Error moving transcode: {e}"); return

def process_show_topdir(con, topdir: Path, downloads_root: Path, output_root: Path, tmdb_key: Optional[str]):
    log(f"Processing show topdir: {topdir}")
    mapping = collect_videos_two_depth(topdir)
    if not mapping:
        log("No videos found inside; skipping.")
        return
    season_needs_copy: Set[str] = set()
    # direct files
    if "" in mapping:
        for vid in mapping[""]:
            process_movie_file(con, vid, downloads_root, output_root)
            st = status_of(con, vid)
            if st in ("skipped_moved","kept_original_moved"):
                season_needs_copy.add("")  # topdir copy
    # seasons
    for season_name, videos in mapping.items():
        if season_name == "": continue
        for vid in videos:
            process_movie_file(con, vid, downloads_root, output_root)
            st = status_of(con, vid)
            if st in ("skipped_moved","kept_original_moved"):
                season_needs_copy.add(season_name)
    # copy full seasons that were flagged
    for sk in list(season_needs_copy):
        if sk == "":
            src_dir = topdir
            dest_dir = output_root / topdir.relative_to(downloads_root)
        else:
            src_dir = topdir / sk
            dest_dir = output_root / (topdir.relative_to(downloads_root) / sk)
        if src_dir.exists():
            log(f"Season-level copy: {src_dir} -> {dest_dir}")
            copy_tree_safe(src_dir, dest_dir)
            for f in src_dir.rglob("*"):
                if f.is_file() and f.suffix.lower() in VIDEO_EXTS:
                    mark(con, f, "copied_season", f"season copied to {dest_dir}")
    # posters: save poster at output root next to show folder
    if not ARGS.no_posters and not ARGS.posters_only:
        out_root = output_root
        fetch_and_save_show_poster(topdir.name, out_root, tmdb_key, downloads_root, posters_only=False)

# ---------- main scan loop ----------
def scan_and_process(downloads: Path, output: Path, poll_interval: int, one_shot: bool=False, tmdb_key: Optional[str]=None):
    ensure_dir(downloads); ensure_dir(output)
    con = init_db()
    log(f"Scanning {downloads} -> {output}")
    try:
        while True:
            any_found = False
            for entry in sorted(downloads.iterdir()):
                if entry.name.startswith("."): continue
                # if posters-only mode: only fetch posters and skip transcode logic
                if ARGS.posters_only:
                    # for top-level folders only
                    if entry.is_dir():
                        any_found = True
                        if ARGS.confirm and not ask_confirm(f"Fetch poster for {entry.name}?"): continue
                        fetch_and_save_show_poster(entry.name, output, tmdb_key, downloads_root=downloads, posters_only=True)
                    continue
                # skip ignored temp entries
                if is_temporary_name(entry.name): continue
                if entry.is_dir():
                    mapping = collect_videos_two_depth(entry)
                    if not mapping: continue
                    any_found = True
                    # sample a file to check stability
                    sample = None
                    for k,v in mapping.items():
                        if v:
                            sample = v[0]; break
                    if sample and not file_is_stable(sample):
                        log(f"Topdir {entry.name} has unstable files; skipping this pass.")
                        continue
                    process_show_topdir(con, entry, downloads, output, tmdb_key)
                elif entry.is_file():
                    if entry.suffix.lower() not in VIDEO_EXTS: continue
                    any_found = True
                    process_movie_file(con, entry, downloads, output)
            if one_shot:
                log("One-shot: exiting.")
                break
            if not any_found:
                log(f"No candidates found. Sleeping {poll_interval}s.")
            time.sleep(poll_interval)
    except KeyboardInterrupt:
        log("User interrupt: exiting.")

# ---------- CLI & entry ----------
def prompt_paths():
    d = input(f"Downloads folder [{DEFAULT_DOWNLOADS}]: ").strip() or DEFAULT_DOWNLOADS
    o = input(f"Output folder [{DEFAULT_OUTPUT}]: ").strip() or DEFAULT_OUTPUT
    poll = input(f"Poll interval seconds [{DEFAULT_POLL_INTERVAL}]: ").strip()
    try: p = int(poll) if poll else DEFAULT_POLL_INTERVAL
    except: p = DEFAULT_POLL_INTERVAL
    return Path(d).expanduser(), Path(o).expanduser(), p

def main():
    global ARGS
    p = ArgumentParser(description="Nomad transcoder + posters (multi-depth show support)")
    p.add_argument("--downloads", help="Downloads folder (skip prompt)")
    p.add_argument("--output", help="Output folder (skip prompt)")
    p.add_argument("--poll", type=int, default=DEFAULT_POLL_INTERVAL, help="Poll interval seconds")
    p.add_argument("--one-shot", action="store_true", help="Scan once and exit")
    p.add_argument("--confirm", action="store_true", help="Ask user to confirm each file before processing (single-fire decisions allowed)")
    p.add_argument("--no-posters", action="store_true", help="Don't fetch posters")
    p.add_argument("--posters-only", action="store_true", help="Only fetch posters for top-level folders, no transcode/move")
    p.add_argument("--reset-db", action="store_true", help="Reset processed-state DB before starting")
    p.add_argument("--dry-run", action="store_true", help="Show actions but don't modify files or network")
    ARGS = p.parse_args()

    if ARGS.reset_db: reset_db()
    if ARGS.downloads:
        downloads = Path(ARGS.downloads).expanduser()
        output = Path(ARGS.output).expanduser() if ARGS.output else Path(DEFAULT_OUTPUT).expanduser()
        poll = ARGS.poll
    else:
        downloads, output, poll = prompt_paths()

    # ensure posters-only and no-posters logical exclusivity
    if ARGS.posters_only and ARGS.no_posters:
        log("Conflict: --posters-only and --no-posters both set. Exiting.")
        sys.exit(1)

    script_dir = Path(__file__).parent
    tmdb_key = None if ARGS.no_posters else load_tmdb_key(script_dir)

    # main run
    scan_and_process(downloads, output, poll, one_shot=ARGS.one_shot, tmdb_key=tmdb_key)

if __name__ == "__main__":
    main()
