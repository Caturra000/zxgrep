#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import re
import sys
import tarfile
import fnmatch
import shutil
import stat
import tempfile
import subprocess
import concurrent.futures
import zipfile
from pathlib import Path
from html.parser import HTMLParser


PROGRAM = "zxgrep"

RED = "\033[01;31m"
CYAN = "\033[01;36m"
RESET = "\033[0m"

# Boundary definition for exact match:
# Characters before/after the keyword cannot be [A-Za-z0-9_]
LEFT_BOUNDARY = r"(?<![0-9A-Za-z_])"
RIGHT_BOUNDARY = r"(?![0-9A-Za-z_])"

SPECIAL_EXTS = ('.pdf', '.epub', '.mobi', '.azw3')


BASH_COMPLETION_SCRIPT = r'''# bash completion for zxgrep
_zxgrep() {
    local cur prev i input_seen
    COMPREPLY=()

    cur="${COMP_WORDS[COMP_CWORD]}"
    if (( COMP_CWORD > 0 )); then
        prev="${COMP_WORDS[COMP_CWORD-1]}"
    else
        prev=""
    fi

    local opts="--help --install --print-bash-completion --clean --file --case-sensitive --exact --regex --or --include --exclude --copy --move --list-files --name-only --color-path --no-color-path --stream --flat --ugrep -h -s -x -r -l -N -o -O -j --jobs"

    if [[ "$prev" == "-o" ]]; then
        compopt -o filenames 2>/dev/null
        mapfile -t COMPREPLY < <(compgen -d -- "$cur")
        return 0
    fi

    if [[ "$prev" == "-j" || "$prev" == "--jobs" ]]; then
        COMPREPLY=()
        return 0
    fi

    if [[ "$prev" == "--include" || "$prev" == "--exclude" ]]; then
        COMPREPLY=()
        return 0
    fi

    if [[ "$cur" == -* ]]; then
        mapfile -t COMPREPLY < <(compgen -W "$opts" -- "$cur")
        return 0
    fi

    input_seen=0
    i=1
    while (( i < COMP_CWORD )); do
        case "${COMP_WORDS[i]}" in
            --help|-h|--install|--print-bash-completion|--clean|--file|--case-sensitive|-s|--exact|-x|--regex|-r|--or|--copy|--move|--list-files|-l|--name-only|-N|--color-path|--no-color-path|--stream|--flat|--ugrep|-O)
                ;;
            -o|-j|--jobs|--include|--exclude)
                ((i++))
                ;;
            --)
                input_seen=1
                break
                ;;
            -*)
                ;;
            *)
                input_seen=1
                break
                ;;
        esac
        ((i++))
    done

    if (( input_seen == 0 )); then
        compopt -o filenames 2>/dev/null
        mapfile -t COMPREPLY < <(compgen -f -- "$cur")
        return 0
    fi

    COMPREPLY=()
    return 0
}

complete -F _zxgrep zxgrep
'''


ZSH_COMPLETION_SCRIPT = r'''#compdef zxgrep
_zxgrep() {
    local opts=(--help -h --install --print-bash-completion --clean --file --case-sensitive -s --exact -x --regex -r --or --include --exclude --copy --move --list-files -l --name-only -N --color-path --no-color-path --stream --flat --ugrep -o -O -j --jobs)
    if [[ $words[CURRENT] == -* ]]; then
        compadd -- "${opts[@]}"
        return
    fi
    _files
}
_zxgrep "$@"
'''


def eprint(*args, **kwargs):
    print(*args, file=sys.stderr, **kwargs)


def die(msg, code=2):
    eprint(f"{PROGRAM}: {msg}")
    raise SystemExit(code)


def usage():
    print(f"""Usage:
  {PROGRAM} INPUT WORD1 [WORD2 ...]
  {PROGRAM} INPUT WORD1 [WORD2 ...] --file
  {PROGRAM} INPUT WORD1 [WORD2 ...] --file -o OUTDIR
  {PROGRAM} INPUT WORD1 [WORD2 ...] -O
  {PROGRAM} INPUT WORD1 [WORD2 ...] -x
  {PROGRAM} INPUT WORD1 [WORD2 ...] -r
  {PROGRAM} INPUT WORD1 [WORD2 ...] -s
  {PROGRAM} INPUT WORD1 [WORD2 ...] --or
  {PROGRAM} INPUT WORD1 [WORD2 ...] --include '*.py' --exclude 'test_*'
  {PROGRAM} INPUT WORD1 [WORD2 ...] -l
  {PROGRAM} INPUT WORD1 [WORD2 ...] -N
  {PROGRAM} INPUT WORD1 [WORD2 ...] -j 8
  {PROGRAM} INPUT WORD1 [WORD2 ...] --stream
  {PROGRAM} INPUT WORD1 [WORD2 ...] -O --flat
  {PROGRAM} INPUT WORD1 [WORD2 ...] --ugrep
  {PROGRAM} --install
  {PROGRAM} --print-bash-completion
  {PROGRAM} --clean

INPUT is auto-detected as:
  1) a *.tar.zst archive
  2) a directory (recursively process text files inside)
  3) a single text file

Notes:
  1) Default mode:
     Search by "line".
     The same line must contain all keywords (AND mode).
     If INPUT is a tar.zst, it will be extracted to a temporary directory first
     (prefers shared memory on Linux if available, otherwise system temp).

  2) --file mode:
     Search by "file".
     The same file must contain all keywords; the keywords do not need to be on the same line.
     By default, output lines that contain any keyword/expression in those files, with highlighting.

  3) Default matching:
     Non-exact + case-insensitive
     i.e., normal substring matching.
     Example keyword exec:
       Can match: exec, execution, EXEC, my_exec_call

  4) -x / --exact:
     Enable exact matching.
     Exact match is defined as:
       characters before/after the keyword cannot be English letters / digits / underscore
     Example keyword exec:
       Matches: " exec ", "(exec)", "exec;"
       Does not match: "execution", "my_exec_var", "exec123"

  5) -r / --regex:
     Enable regex matching.
     Each WORD is treated as a regular expression.
     Multiple keywords are still supported.
     Matching is still line-based; multi-line regex across lines is not supported.

  6) -s / --case-sensitive:
     Enable case-sensitive matching.

  7) --or:
     Use OR logic to connect multiple keywords (default is AND).
     - Default (AND): must contain all keywords
     - OR mode: match if any keyword is present
     Example:
       {PROGRAM} ./docs exec task        # AND: must contain both exec and task
       {PROGRAM} ./docs exec task --or   # OR:  contains exec or task

  8) --include GLOB:
     Only search files whose basename matches the specified glob pattern.
     Matching is based on the file basename (without directories).
     Can be specified multiple times to add multiple patterns (any match is accepted).
     Example:
       {PROGRAM} ./docs exec --include '*.py'
       {PROGRAM} ./docs exec --include '*.py' --include '*.js'

  9) --exclude GLOB:
     Exclude files whose names match the specified glob pattern.
     Matches against basename and relative path (either match excludes).
     Can be specified multiple times to add multiple patterns.
     Example:
       {PROGRAM} ./docs exec --exclude '*.log'
       {PROGRAM} ./docs exec --exclude 'node_modules' --exclude '*.min.js'

  10) -l / --list-files:
      Only list matched file paths, do not output matched lines.
      - In default mode: list files that have at least one line matching all keywords
      - In --file mode: list files that contain all keywords

  11) -N / --name-only:
      Search only on the "filename itself", not file contents.
      Here "filename" means basename, excluding parent directories.
      This mode automatically operates at file level and only outputs matched file paths.
      Also supports -o/-O, --copy, --move.
      Example:
        {PROGRAM} ./docs report -N
        {PROGRAM} ./docs 'report.*2024' -N -r

  12) Default output includes line and column numbers:
      Like:
        path/to/file.txt:12:8: matched line

  13) Path coloring:
      Paths are colored by default.
      To avoid affecting VSCode's path:line:col recognition, you may disable path coloring.
      Disable:
        --no-color-path
      Explicitly enable (default behavior):
        --color-path

  14) -o / -O:
      Output matched files into a target directory (does not change matching behavior).
      Default behavior is "copy".
      To switch to move, add:
        --move

      Related options:
        --copy   explicitly copy (default)
        --move   move instead

      To avoid name collisions, the relative directory structure is preserved.
      - For tar.zst: preserve paths inside the archive
      - For a directory: preserve paths relative to the input directory
      - For a single file: output as same filename under the target directory

  15) --flat:
      Flatten output directory structure (only effective with -o or -O).
      Instead of preserving the original directory hierarchy, all matched files
      are placed directly in the target directory (single level).
      If multiple files have the same name, conflicts are resolved by appending
      .conflict-N before the file extension.
      Example:
        {PROGRAM} ./docs exec task -O --flat
        # Results in: zxgrep_exec+task/file1.txt, zxgrep_exec+task/file1.conflict-1.txt

  16) Text file detection:
      For directory/single-file input, obvious binary files are skipped (simple NUL-byte check).

  17) -j / --jobs:
      Specify number of parallel worker processes.
      Default uses CPU core count.
      Search uses multi-process parallelism; output is streamed in real time (order not guaranteed).
      Example:
        {PROGRAM} ./docs exec task -j 8
        {PROGRAM} archive.tar.zst exec -j 4

  18) --stream:
      Stream processing for tar.zst archives.
      Instead of extracting the entire archive to a temporary directory,
      process files one by one directly from the tar stream.
      Avoids high temporary disk usage for large archives.
      For directory or single-file inputs, this flag has no effect.
      Note: -j/--jobs is ignored in stream mode (processing is sequential).

  19) --install:
      Install to /usr/local/bin/zxgrep and bash completion (Unix).
      On Windows, creates zxgrep.cmd launcher and adds to user PATH.

  20) --clean:
      Clean up all auto-generated output directories in the current directory (prefixed with zxgrep_).
      You will be prompted for confirmation before deletion.

  21) PDF support:
      Files ending in .pdf are automatically extracted and searched.
      Requires the 'pdftotext' command (part of Poppler):
        Linux:   sudo apt install poppler-utils
        Windows: choco install poppler (or scoop install poppler)
      If 'pdftotext' is not installed, PDF files are silently skipped.

  22) eBook support (EPUB / MOBI / AZW3):
      - .epub files are parsed natively using the Python standard library (no external
        dependencies). HTML/XHTML content inside the EPUB archive is extracted as plain text.
      - .mobi and .azw3 files require the 'ebook-convert' command (part of Calibre):
          Linux:   sudo apt install calibre
          Windows: choco install calibre
        If 'ebook-convert' is not installed, MOBI/AZW3 files are silently skipped.

  23) --ugrep:
      Delegate text search to the 'ugrep' command for significantly better performance.
      Requires 'ugrep' to be installed:
        Linux:   sudo apt install ugrep
        Windows: choco install ugrep
      If 'ugrep' is not found, an error is raised.
      The following features automatically fall back to the built-in Python engine
      because ugrep does not support them natively or mapping is unreliable:
        - --stream mode
        - --name-only mode
        - --file mode with AND logic (cross-line AND)
        - PDF / EPUB / MOBI / AZW3 files (handled by Python, then merged)

Examples:
  {PROGRAM} archive.tar.zst exec task
  {PROGRAM} ./docs exec task
  {PROGRAM} ./docs/a.txt exec task
  {PROGRAM} ./docs/report.pdf exec task
  {PROGRAM} ./docs/book.epub hello world
  {PROGRAM} ./docs/book.mobi chapter
  {PROGRAM} ./docs/book.azw3 introduction
  {PROGRAM} archive.tar.zst exec task --file
  {PROGRAM} ./docs exec task -O
  {PROGRAM} ./docs exec task --exact
  {PROGRAM} ./docs 'exec(ute)?' --regex
  {PROGRAM} ./docs 'exec.*task' --regex -s
  {PROGRAM} ./docs exec task --or
  {PROGRAM} ./docs exec task --or -l
  {PROGRAM} ./docs exec --include '*.py'
  {PROGRAM} ./docs exec --include '*.py' --include '*.js' --exclude 'test_*'
  {PROGRAM} ./docs exec task -l
  {PROGRAM} ./docs exec task -O --move
  {PROGRAM} ./docs exec task -O --flat
  {PROGRAM} ./docs report -N
  {PROGRAM} ./docs 'report.*2024' -N -r
  {PROGRAM} ./docs exec task -j 4
  {PROGRAM} archive.tar.zst exec task --stream
  {PROGRAM} ./docs exec task --ugrep
  {PROGRAM} ./docs exec task --ugrep --file --or
  {PROGRAM} --clean
""")


# Options registry: (long, short, takes_value, accumulative, default, conflicts)

_OPTIONS = [
    ("--help",                  "-h", False, False, False,  None),
    ("--install",               None, False, False, False,  None),
    ("--print-bash-completion", None, False, False, False,  None),
    ("--clean",                 None, False, False, False,  None),
    ("--file",                  None, False, False, False,  None),
    ("--case-sensitive",        "-s", False, False, False,  None),
    ("--exact",                 "-x", False, False, False,  ("--regex",)),
    ("--regex",                 "-r", False, False, False,  ("--exact",)),
    ("--or",                    None, False, False, False,  None),
    ("--include",               None, True,  True,  [],     None),
    ("--exclude",               None, True,  True,  [],     None),
    ("--list-files",            "-l", False, False, False,  None),
    ("--name-only",             "-N", False, False, False,  None),
    ("--color-path",            None, False, False, True,   None),
    ("--no-color-path",         None, False, False, False,  None),
    ("--copy",                  None, False, False, False,  ("--move",)),
    ("--move",                  None, False, False, False,  ("--copy",)),
    ("--outdir",                "-o", True,  False, None,   ("--auto-outdir",)),
    ("--auto-outdir",           "-O", False, False, False,  ("--outdir",)),
    ("--jobs",                  "-j", True,  False, None,   None),
    ("--stream",                None, False, False, False,  None),
    ("--flat",                  None, False, False, False,  None),
    ("--ugrep",                 None, False, False, False,  None),
]

_OPT_BY_FLAG = {}
for _o in _OPTIONS:
    _OPT_BY_FLAG[_o[0]] = _o
    if _o[1]:
        _OPT_BY_FLAG[_o[1]] = _o

_STANDALONE = {
    "-h": "help", "--help": "help",
    "--install": "install",
    "--print-bash-completion": "print-completion",
    "--clean": "clean",
}

_ACTION_FLAGS = ("--install", "--print-bash-completion", "--clean")

_DERIVATIONS = [
    # (trigger, target, value)
    ("--name-only", "--file", True),
    ("--name-only", "--list-files", True),
]

_REQUIRES_OUTDIR = ("--flat", "--copy", "--move")


def _parse_jobs(raw):
    try:
        j = int(raw)
        if j < 1: raise ValueError
        return j
    except (ValueError, TypeError):
        die(f"Invalid process count: {raw}")


def _parse(argv):
    if not argv:
        usage(); raise SystemExit(1)

    if len(argv) == 1 and argv[0] in _STANDALONE:
        return {"action": _STANDALONE[argv[0]]}

    args = {}
    for o in _OPTIONS:
        long, _, _, accum, default, _ = o
        args[long] = list(default) if accum else default

    words, input_path, stop = [], None, False
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg == "--":
            stop = True; i += 1; continue
        if not stop and arg in _OPT_BY_FLAG:
            opt = _OPT_BY_FLAG[arg]
            long, _, takes_val, accum, _, _ = opt
            if takes_val:
                i += 1
                if i >= len(argv):
                    die(f"{arg} requires a value")
                if accum:
                    args[long].append(argv[i])
                else:
                    args[long] = argv[i]
            else:
                args[long] = True
            i += 1; continue
        if not stop and arg.startswith("-"):
            die(f"Unsupported option: {arg}")
        if input_path is None:
            input_path = arg
        else:
            words.append(arg)
        i += 1

    if args["--help"]:
        usage(); raise SystemExit(0)
    conflict = next((f for f in _ACTION_FLAGS if args[f]), None)
    if conflict:
        die(f"{conflict} cannot be used together with search arguments")
    if input_path is None:
        die("Missing INPUT")
    if not words:
        die("At least one keyword/expression is required")
    if any(w == "" for w in words):
        die("Keyword/expression cannot be empty")

    for o in _OPTIONS:
        long, _, _, _, _, conflicts = o
        if conflicts and args[long]:
            for c in conflicts:
                if args[c]:
                    die(f"{long} and {c} cannot be used together")

    for trigger, target, value in _DERIVATIONS:
        if args[trigger]:
            args[target] = value

    if args["--no-color-path"]:
        args["--color-path"] = False

    if args["--auto-outdir"]:
        safe = [re.sub(r"[^\w._+-]", "_", w) for w in words]
        args["--outdir"] = os.path.abspath(f"zxgrep_{'+'.join(safe)}")

    outdir = Path(os.path.abspath(os.path.expanduser(str(args["--outdir"])))) if args["--outdir"] else None

    for flag in _REQUIRES_OUTDIR:
        if args[flag] and outdir is None:
            die(f"{flag} can only be used with -o or -O")

    jobs = _parse_jobs(args["--jobs"]) if args["--jobs"] is not None else (os.cpu_count() or 4)

    filters = None
    if args["--include"] or args["--exclude"]:
        filters = {"include": args["--include"], "exclude": args["--exclude"]}

    mode = "exact" if args["--exact"] else "regex" if args["--regex"] else "substr"

    return {
        "action": "search",
        "input": Path(os.path.abspath(os.path.expanduser(input_path))),
        "words": words, "file": args["--file"], "outdir": outdir,
        "case": args["--case-sensitive"], "mode": mode, "or": args["--or"],
        "list": args["--list-files"], "name": args["--name-only"],
        "move": args["--move"], "color": args["--color-path"],
        "jobs": jobs, "filters": filters, "stream": args["--stream"],
        "flat": args["--flat"], "ugrep": args["--ugrep"],
    }


# File utilities

def _is_within(path, parent):
    try:
        return os.path.commonpath([os.path.abspath(path), os.path.abspath(parent)]) == os.path.abspath(parent)
    except ValueError:
        return False


def _display(path):
    p = Path(os.path.abspath(path))
    try:
        rel = p.relative_to(Path(os.path.abspath("."))).as_posix()
        return "./" + rel if rel != "." else "."
    except ValueError:
        return p.as_posix()


def _is_probably_text(path):
    try:
        with open(path, "rb") as f:
            return b"\x00" not in f.read(8192)
    except Exception:
        return False


def _safe_transfer(src, dst, do_move):
    src, dst = Path(os.path.abspath(src)), Path(os.path.abspath(dst))
    if os.path.abspath(src) == os.path.abspath(dst):
        return
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists() or dst.is_symlink():
        if dst.is_dir() and not dst.is_symlink():
            shutil.rmtree(dst)
        else:
            dst.unlink()
    (shutil.move if do_move else shutil.copy2)(str(src), str(dst))


def _resolve_flat(outdir, rel):
    name = Path(rel).name
    target = outdir / name
    if not target.exists():
        return target
    stem, suffix = Path(name).stem, Path(name).suffix
    for n in range(1, 10000):
        target = outdir / f"{stem}.conflict-{n}{suffix}"
        if not target.exists():
            return target


def _pick_tmp_root():
    if sys.platform != "win32":
        shm = Path("/dev/shm")
        if shm.exists() and os.access(str(shm), os.W_OK):
            return shm
    return Path(tempfile.gettempdir())


def _detect(path):
    p = Path(os.path.abspath(os.path.expanduser(str(path))))
    if not p.exists():
        die(f"Input does not exist: {p}")
    if p.is_dir():
        return {"kind": "dir", "path": p}
    if p.is_file():
        return {"kind": "archive" if p.name.endswith(".tar.zst") else "file", "path": p}
    die(f"Unsupported input type: {p}")


def _extract_archive(archive, dest):
    shutil.which("zstd") or die("Missing required command: zstd")
    proc = subprocess.Popen(["zstd", "-d", "-T0", str(archive), "-c"], stdout=subprocess.PIPE)
    try:
        with tarfile.open(fileobj=proc.stdout, mode="r|") as tf:
            kwargs = {"filter": "data"} if sys.version_info >= (3, 12) else {}
            tf.extractall(path=str(dest), **kwargs)
    except Exception:
        proc.terminate(); proc.wait(); raise
    proc.wait()
    if proc.returncode != 0:
        die(f"zstd decompression failed with exit code {proc.returncode}")


# File iteration

def _should_include(rel, filters):
    if not filters:
        return True
    name = Path(rel).name
    for pat in filters.get("exclude", []):
        if fnmatch.fnmatch(name, pat) or fnmatch.fnmatch(rel, pat):
            return False
    inc = filters.get("include", [])
    return not inc or any(fnmatch.fnmatch(name, p) for p in inc)


def _walk_dir(root, filters, exclude=None):
    root = os.path.abspath(root)
    ex = os.path.abspath(exclude) if exclude else None
    for cur, dirs, files in os.walk(root, followlinks=False):
        if ex:
            dirs[:] = sorted(d for d in dirs if not _is_within(os.path.join(cur, d), ex))
        else:
            dirs[:] = sorted(dirs)
        for name in sorted(files):
            p = os.path.join(cur, name)
            try:
                st = os.lstat(p)
            except OSError:
                continue
            if not stat.S_ISREG(st.st_mode):
                continue
            if ex and _is_within(p, ex):
                continue
            rel = os.path.relpath(p, root).replace(os.sep, "/")
            if not _should_include(rel, filters):
                continue
            yield {"rel": rel, "path": p, "display": _display(p)}


def _walk_archive(root, filters):
    root = Path(os.path.abspath(root))
    for cur, dirs, files in os.walk(str(root), followlinks=False):
        dirs[:] = sorted(dirs)
        for name in sorted(files):
            p = Path(cur) / name
            if p.is_symlink() or not p.is_file():
                continue
            rel = p.relative_to(root).as_posix()
            if not _should_include(rel, filters):
                continue
            yield {"rel": rel, "path": str(p), "display": rel}


def _walk_single(path):
    p = Path(os.path.abspath(os.path.expanduser(str(path))))
    if p.is_file():
        yield {"rel": p.name, "path": str(p), "display": _display(p)}


def _find_specials(info, extracted, filters, exclude):
    root = str(extracted if extracted else info["path"])
    results = []
    for cur, dirs, files in os.walk(root, followlinks=False):
        if exclude:
            dirs[:] = [d for d in dirs if not _is_within(os.path.join(cur, d), str(exclude))]
        for name in files:
            if not (name.endswith(".pdf") or name.endswith(".epub") or
                    name.endswith(".mobi") or name.endswith(".azw3")):
                continue
            p = os.path.join(cur, name)
            if os.path.islink(p) or not os.path.isfile(p):
                continue
            pp = Path(p)
            if info["kind"] == "archive":
                rel = pp.relative_to(extracted).as_posix()
                display = rel
            elif info["kind"] == "dir":
                if exclude and _is_within(p, str(exclude)):
                    continue
                rel = pp.relative_to(info["path"]).as_posix()
                display = _display(pp)
            else:
                rel = pp.name
                display = _display(pp)
            if not _should_include(rel, filters):
                continue
            results.append({"rel": rel, "path": p, "display": display})
    return results


# Pattern compilation

_MODE_EXPR = {
    "substr": lambda w: re.escape(w),
    "exact":  lambda w: LEFT_BOUNDARY + re.escape(w) + RIGHT_BOUNDARY,
    "regex":  lambda w: w,
}


def _compile_one(word, mode, case):
    flags = 0 if case else re.IGNORECASE
    try:
        pat = re.compile(_MODE_EXPR[mode](word), flags)
    except re.error as ex:
        die(f"Regex compilation failed: {word!r}: {ex}")
    if mode == "regex":
        m = pat.search("")
        if m is not None and m.start() == m.end():
            die(f"Regex is not allowed to match empty string: {word!r}")
    return pat


def _compile_all(words, mode, case):
    return [_compile_one(w, mode, case) for w in words]


def _compile_any(words, mode, case):
    flags = 0 if case else re.IGNORECASE
    if mode == "substr":
        uniq = sorted(dict.fromkeys(words), key=len, reverse=True)
        expr = "|".join(re.escape(w) for w in uniq)
    elif mode == "exact":
        uniq = sorted(dict.fromkeys(words), key=len, reverse=True)
        expr = LEFT_BOUNDARY + "(?:" + "|".join(re.escape(w) for w in uniq) + ")" + RIGHT_BOUNDARY
    else:
        expr = "|".join(f"(?:{w})" for w in words)
    try:
        return re.compile(expr, flags)
    except re.error as ex:
        die(f"Highlight regex compilation failed: {ex}")


# Output helpers

def _colorize(line, pat):
    return pat.sub(lambda m: f"{RED}{m.group(0)}{RESET}", line.rstrip("\r\n"))


def _column(line, pat):
    m = pat.search(line)
    return (m.start() + 1) if m else 1


def _label(path, ln, cn, color, tty):
    text = f"{path}:{ln}:{cn}"
    return f"{CYAN}{text}{RESET}" if color and tty else text


def _output(item, matches, outdir, do_move, color, tty, is_list, is_name, any_pat, flat):
    if outdir:
        target = _resolve_flat(outdir, item["rel"]) if flat else outdir / item["rel"]
        _safe_transfer(item["path"], target, do_move)
        display = _display(target)
    else:
        display = item["display"]

    if is_list or is_name:
        text = f"{CYAN}{display}{RESET}" if color and tty else display
        sys.stdout.write(text + "\n")
        sys.stdout.flush()
    else:
        for ln, cn, line in matches:
            prefix = _label(display, ln, cn, color, tty)
            colored = _colorize(line, any_pat) if tty else line.rstrip("\r\n")
            sys.stdout.write(f"{prefix}: {colored}\n")
            sys.stdout.flush()


# Special file extraction

def _extract_pdf(path):
    if not shutil.which("pdftotext"):
        return None
    fd, tmp = tempfile.mkstemp(suffix=".txt", prefix="zxg_pdf_")
    try:
        os.close(fd)
        r = subprocess.run(["pdftotext", "-enc", "UTF-8", "-layout", str(path), tmp], capture_output=True)
        if r.returncode != 0:
            return None
        with open(tmp, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
        return lines or None
    except Exception:
        return None
    finally:
        try: os.unlink(tmp)
        except OSError: pass


class _EPUBParser(HTMLParser):
    _BLOCK = frozenset({
        'p', 'div', 'br', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'li', 'tr',
        'blockquote', 'section', 'article', 'header', 'footer', 'nav', 'aside',
        'main', 'figure', 'figcaption', 'details', 'summary', 'dl', 'dt', 'dd',
        'ul', 'ol', 'table', 'thead', 'tbody', 'tfoot', 'th', 'td', 'hr', 'pre',
    })
    _SKIP = frozenset({'script', 'style', 'head'})

    def __init__(self):
        super().__init__()
        self._parts = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        t = tag.lower()
        if t in self._SKIP: self._skip += 1
        if t in self._BLOCK: self._parts.append('\n')

    def handle_endtag(self, tag):
        t = tag.lower()
        if t in self._SKIP: self._skip = max(0, self._skip - 1)
        if t in self._BLOCK: self._parts.append('\n')

    def handle_data(self, data):
        if self._skip == 0:
            self._parts.append(data)


def _extract_epub(path):
    try:
        with zipfile.ZipFile(path, "r") as zf:
            files = sorted(n for n in zf.namelist()
                           if n.lower().endswith(('.html', '.htm', '.xhtml'))
                           and not n.startswith('META-INF/') and n != 'mimetype')
            parser = _EPUBParser()
            for cf in files:
                try:
                    raw = zf.read(cf)
                    text = None
                    for enc in ('utf-8', 'latin-1'):
                        try:
                            text = raw.decode(enc); break
                        except UnicodeDecodeError:
                            continue
                    if text:
                        parser.feed(text)
                except Exception:
                    continue
            full = ''.join(parser._parts)
            lines = [l + '\n' for l in full.split('\n')]
            return lines if any(l.strip() for l in lines) else None
    except Exception:
        return None


def _extract_ebook(path):
    if not shutil.which("ebook-convert"):
        return None
    fd, tmp = tempfile.mkstemp(suffix=".txt", prefix="zxg_eb_")
    try:
        os.close(fd)
        r = subprocess.run(["ebook-convert", str(path), tmp], capture_output=True, timeout=120)
        if r.returncode != 0:
            return None
        with open(tmp, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
        return lines or None
    except Exception:
        return None
    finally:
        try: os.unlink(tmp)
        except OSError: pass


_EXTRACTORS = {
    ".pdf": _extract_pdf,
    ".epub": _extract_epub,
    ".mobi": _extract_ebook,
    ".azw3": _extract_ebook,
}


def _extract_lines(path):
    return _EXTRACTORS.get(Path(path).suffix.lower(), lambda _: None)(path)


# Search worker

def _process_file(args):
    item, all_pats, any_pat, opts = args
    path = item["path"]
    combine = any if opts["or"] else all

    if opts["name"]:
        name = Path(item["rel"]).name
        matched = combine(p.search(name) for p in all_pats)
        return (item, []) if matched else None

    is_special = Path(path).suffix.lower() in SPECIAL_EXTS
    if not is_special and not _is_probably_text(path):
        return None

    try:
        if opts["file"]:
            found = set()
            lines_cache = []

            if is_special:
                lines_cache = _extract_lines(path)
                if lines_cache is None:
                    return None
                for line in lines_cache:
                    for i, p in enumerate(all_pats):
                        if p.search(line):
                            found.add(i)
            else:
                with open(path, "r", encoding="utf-8", errors="replace", newline="") as f:
                    for line in f:
                        lines_cache.append(line)
                        for i, p in enumerate(all_pats):
                            if p.search(line):
                                found.add(i)

            if opts["or"]:
                if not found:
                    return None
            else:
                if found != set(range(len(all_pats))):
                    return None

            if opts["list"]:
                return (item, [])

            matches = []
            for ln, line in enumerate(lines_cache, 1):
                if any_pat.search(line):
                    matches.append((ln, _column(line, any_pat), line))
            return (item, matches)

        else:
            matches = []

            if is_special:
                special_lines = _extract_lines(path)
                if special_lines is None:
                    return None
                for ln, line in enumerate(special_lines, 1):
                    if combine(p.search(line) for p in all_pats):
                        if opts["list"]:
                            return (item, [])
                        matches.append((ln, _column(line, any_pat), line))
            else:
                with open(path, "r", encoding="utf-8", errors="replace", newline="") as f:
                    for ln, line in enumerate(f, 1):
                        if combine(p.search(line) for p in all_pats):
                            if opts["list"]:
                                return (item, [])
                            matches.append((ln, _column(line, any_pat), line))

            return (item, matches) if matches else None

    except Exception:
        return None


def _process_batch(batch_args):
    items, all_pats, any_pat, opts = batch_args
    results = []
    for item in items:
        result = _process_file((item, all_pats, any_pat, opts))
        if result is not None:
            results.append(result)
    return results


# Search engines

def _run_stream(info, all_pats, any_pat, args, callback):
    shutil.which("zstd") or die("Missing required command: zstd")
    proc = subprocess.Popen(["zstd", "-d", "-T0", str(info["path"]), "-c"], stdout=subprocess.PIPE)
    tmp = Path(tempfile.mkdtemp(prefix="zxgrep_stream."))
    opts = {"file": args["file"], "list": args["list"], "name": args["name"], "or": args["or"]}
    try:
        with tarfile.open(fileobj=proc.stdout, mode="r|") as tf:
            for member in tf:
                if not member.isfile():
                    continue
                rel = member.name[2:] if member.name.startswith("./") else member.name
                if not _should_include(rel, args["filters"]):
                    continue
                ef = tf.extractfile(member)
                if ef is None:
                    continue
                tp = tmp / "current"
                with open(str(tp), "wb") as f:
                    shutil.copyfileobj(ef, f)
                callback(_process_file(({"rel": rel, "path": str(tp), "display": rel},
                                        all_pats, any_pat, opts)))
                try: tp.unlink()
                except FileNotFoundError: pass
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
        proc.terminate(); proc.wait()


def _run_python(items, all_pats, any_pat, args, callback):
    opts = {"file": args["file"], "list": args["list"], "name": args["name"], "or": args["or"]}
    jobs = args["jobs"]
    n = len(items)
    chunk_size = max(1, n // (jobs * 4))
    batches = []
    for i in range(0, n, chunk_size):
        batches.append((items[i:i + chunk_size], all_pats, any_pat, opts))
    with concurrent.futures.ProcessPoolExecutor(max_workers=jobs) as pool:
        futures = {pool.submit(_process_batch, b): b for b in batches}
        for future in concurrent.futures.as_completed(futures):
            try:
                for result in future.result():
                    callback(result)
            except Exception:
                pass

def _make_ugrep_item(fpath, info, extracted):
    p = Path(os.path.abspath(fpath))
    if not p.exists():
        return None
    if info["kind"] == "archive":
        if extracted is None:
            return None
        rel = p.relative_to(extracted).as_posix()
        display = rel
    elif info["kind"] == "dir":
        try:
            rel = p.relative_to(info["path"]).as_posix()
        except ValueError:
            rel = p.name
        display = _display(p)
    else:
        rel = p.name
        display = _display(p)
    return {"rel": rel, "path": str(p), "display": display}


_UGREP_LINE_RE = re.compile(r"^(.*?):(\d+):(\d+):(.*)$", re.DOTALL)

def _run_ugrep(info, extracted, args, callback):
    if not shutil.which("ugrep"):
        die("Missing required command: ugrep")

    root = (extracted if extracted else info["path"]).as_posix()
    cmd = ["ugrep", "--no-messages", "--binary-files=without-match", "-H"]
    if info["kind"] in ("dir", "archive"):
        cmd.append("-r")
    if not args["case"]:
        cmd.append("-i")
    if args["mode"] == "exact":
        cmd.append("-w")
    for ext in SPECIAL_EXTS:
        cmd.extend(["-g", f"!*{ext}"])
    f = args["filters"]
    if f:
        for p in f.get("include", []):
            cmd.extend(["-g", p])
        for p in f.get("exclude", []):
            cmd.extend(["-g", f"!{p}"])
    if args["list"]:
        cmd.append("-l")
    else:
        cmd.extend(["-n", "-k", "--color=never"])
    if args["or"]:
        for w in args["words"]:
            cmd.extend(["-e", w])
    else:
        for i, w in enumerate(args["words"]):
            cmd.extend(["-e", w])
            if i < len(args["words"]) - 1:
                cmd.append("--and")
    cmd.append(root)

    proc = subprocess.run(cmd, capture_output=True, encoding="utf-8", errors="replace")
    if proc.returncode == 2:
        err = (proc.stderr or "").strip() or "unknown error"
        eprint(f"{PROGRAM}: ugrep error: {err}")
        eprint(f"{PROGRAM}: falling back to Python engine")
        return False
    if proc.returncode != 0:
        return True

    stdout = proc.stdout or ""
    if args["list"]:
        for fp in stdout.splitlines():
            item = _make_ugrep_item(fp, info, extracted)
            if item:
                callback((item, []))
    else:
        results = {}
        for line in stdout.splitlines():
            m = _UGREP_LINE_RE.match(line)
            if not m:
                continue
            fp, ln, cn, text = m.group(1), int(m.group(2)), int(m.group(3)), m.group(4)
            if fp not in results:
                item = _make_ugrep_item(fp, info, extracted)
                if item:
                    results[fp] = (item, [])
                else:
                    continue
            results[fp][1].append((ln, cn, text + "\n"))
        for item, matches in results.values():
            callback((item, matches))
    return True


def _run(args):
    info = _detect(args["input"])
    all_pats = _compile_all(args["words"], args["mode"], args["case"])
    any_pat = _compile_any(args["words"], args["mode"], args["case"])
    outdir = args["outdir"]
    tty = sys.stdout.isatty()

    if outdir:
        outdir.mkdir(parents=True, exist_ok=True)

    found = False

    def callback(result):
        nonlocal found
        if result is None:
            return
        found = True
        _output(result[0], result[1], outdir, args["move"], args["color"], tty,
                args["list"], args["name"], any_pat, args["flat"])

    if info["kind"] == "archive" and args["stream"]:
        _run_stream(info, all_pats, any_pat, args, callback)
        if outdir and found:
            eprint(f"Matched files have been {'moved' if args['move'] else 'copied'} to: {_display(outdir)}")
        return found

    temp_root = None
    try:
        extracted = None
        if info["kind"] == "archive":
            temp_root = Path(tempfile.mkdtemp(prefix="zxgrep.", dir=str(_pick_tmp_root())))
            _extract_archive(info["path"], temp_root)
            extracted = temp_root

        exclude = None
        if info["kind"] == "dir" and outdir:
            if _is_within(str(outdir), str(info["path"])):
                exclude = outdir

        use_ugrep = args["ugrep"] and not args["stream"] and not args["name"]
        if use_ugrep and args["file"] and not args["or"]:
            use_ugrep = False

        ugrep_ok = False
        if use_ugrep:
            ugrep_ok = _run_ugrep(info, extracted, args, callback)

        if not ugrep_ok:
            if info["kind"] == "archive":
                items = list(_walk_archive(extracted, args["filters"]))
            elif info["kind"] == "dir":
                items = list(_walk_dir(info["path"], args["filters"], exclude))
            else:
                items = list(_walk_single(args["input"]))
            if items:
                _run_python(items, all_pats, any_pat, args, callback)
        else:
            specials = _find_specials(info, extracted, args["filters"], exclude)
            if specials:
                _run_python(specials, all_pats, any_pat, args, callback)

        if outdir and found:
            eprint(f"Matched files have been {'moved' if args['move'] else 'copied'} to: {_display(outdir)}")
        return found

    finally:
        if temp_root:
            shutil.rmtree(temp_root, ignore_errors=True)


# Installation

_COMP_DIRS = [
    Path("/usr/share/bash-completion/completions"),
    Path("/usr/local/share/bash-completion/completions"),
    Path("/opt/homebrew/share/bash-completion/completions"),
    Path("/etc/bash_completion.d"),
]


_ZSH_COMP_DIRS = [
    Path("/usr/share/zsh/site-functions"),
    Path("/usr/local/share/zsh/site-functions"),
    Path("/opt/homebrew/share/zsh/site-functions"),
]


def _complete_dir():
    for d in _COMP_DIRS:
        if d.is_dir():
            return d / PROGRAM
    return _COMP_DIRS[1] / PROGRAM


def _zsh_comp_dir():
    for d in _ZSH_COMP_DIRS:
        if d.is_dir():
            return d / "_zxgrep"
    return None


def _install_file(src, dst, mode, sudo):
    shutil.which("install") or die("Missing required command: install")
    if sudo:
        shutil.which("sudo") or die("Missing required command: sudo")
        subprocess.run(["sudo", "mkdir", "-p", str(dst.parent)], check=True)
        subprocess.run(["sudo", "install", "-m", f"{mode:o}", str(src), str(dst)], check=True)
    else:
        dst.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(["install", "-m", f"{mode:o}", str(src), str(dst)], check=True)


def _install_unix():
    self_path = Path(__file__).resolve()
    bin_target = Path("/usr/local/bin") / PROGRAM
    comp_target = _complete_dir()
    zsh_target = _zsh_comp_dir()
    sudo = (os.geteuid() != 0)
    tmp = None

    targets = {comp_target, zsh_target} - {None}
    for d, name in [(d, PROGRAM) for d in _COMP_DIRS] + [(d, "_zxgrep") for d in _ZSH_COMP_DIRS]:
        old = d / name
        if old.exists() and old not in targets:
            subprocess.run((["sudo"] if sudo else []) + ["rm", "-f", str(old)], capture_output=True)

    try:
        fd, tmp_name = tempfile.mkstemp(prefix="zxgrep_completion_", text=True)
        os.close(fd)
        tmp = Path(tmp_name)

        _install_file(self_path, bin_target, 0o755, sudo)
        print(f"Installed main program to: {bin_target}")

        tmp.write_text(BASH_COMPLETION_SCRIPT, encoding="utf-8")
        _install_file(tmp, comp_target, 0o644, sudo)
        print(f"Installed Bash completion to: {comp_target}")

        zsh_hint = ""
        if zsh_target:
            tmp.write_text(ZSH_COMPLETION_SCRIPT, encoding="utf-8")
            _install_file(tmp, zsh_target, 0o644, sudo)
            print(f"Installed Zsh completion to: {zsh_target}")
            zsh_hint = "\n  Zsh:  autoload -U compinit && compinit"

        print(f"\nIf completion still doesn't work, open a new shell or:\n  Bash: source {comp_target}{zsh_hint}")
    finally:
        if tmp and tmp.exists():
            try: tmp.unlink()
            except Exception: pass


def _find_git_bash_dir():
    candidates = []
    for env in ("ProgramFiles", "ProgramW6432", "ProgramFiles(x86)"):
        pf = os.environ.get(env)
        if pf:
            candidates.append(Path(pf) / "Git")
    try:
        r = subprocess.run(["git", "--exec-path"], capture_output=True, text=True, timeout=5)
        if r.returncode == 0 and r.stdout.strip():
            candidates.append(Path(r.stdout.strip()).parent.parent.parent)
    except Exception:
        pass
    seen = set()
    for gd in candidates:
        norm = os.path.normcase(str(gd))
        if norm in seen:
            continue
        seen.add(norm)
        d = gd / "usr" / "share" / "bash-completion" / "completions"
        if d.is_dir() and os.access(str(d), os.W_OK):
            return d
    return None


def _install_completion_windows():
    sys_dir = _find_git_bash_dir()
    if sys_dir is not None:
        target = sys_dir / PROGRAM
        target.write_text(BASH_COMPLETION_SCRIPT, encoding="utf-8", newline="\n")
        print(f"Installed Bash completion to: {target}")
        return

    home = Path.home()
    comp_dir = home / ".bash_completion.d"
    comp_dir.mkdir(parents=True, exist_ok=True)
    target = comp_dir / PROGRAM
    target.write_text(BASH_COMPLETION_SCRIPT, encoding="utf-8", newline="\n")
    print(f"Installed Bash completion to: {target}")

    source_line = f"[ -f ~/.bash_completion.d/{PROGRAM} ] && . ~/.bash_completion.d/{PROGRAM}"
    bashrc = home / ".bashrc"
    need_append = True
    if bashrc.exists():
        if PROGRAM in bashrc.read_text(encoding="utf-8", errors="replace"):
            need_append = False
    if need_append:
        with open(str(bashrc), "a", encoding="utf-8", newline="\n") as f:
            f.write(f"\n{source_line}\n")
        print(f"Added source line to: {bashrc}")

    bash_profile = home / ".bash_profile"
    if bash_profile.exists():
        bp_text = bash_profile.read_text(encoding="utf-8", errors="replace")
        if "bashrc" not in bp_text and PROGRAM not in bp_text:
            with open(str(bash_profile), "a", encoding="utf-8", newline="\n") as f:
                f.write(f"\n{source_line}\n")
            print(f"Added source line to: {bash_profile}")
    else:
        bash_profile.write_text(
            '# ~/.bash_profile\nif [ -f ~/.bashrc ]; then\n  . ~/.bashrc\nfi\n',
            encoding="utf-8", newline="\n")
        print(f"Created: {bash_profile}")


def _add_to_user_path_windows(directory):
    try:
        import winreg
    except ImportError:
        return False
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Environment", 0,
                             winreg.KEY_READ | winreg.KEY_WRITE)
        try:
            cur, _ = winreg.QueryValueEx(key, "PATH")
            paths = [os.path.normcase(p) for p in cur.split(os.pathsep) if p]
            if os.path.normcase(directory) in paths:
                return True
            new = cur.rstrip(os.pathsep) + os.pathsep + directory
            winreg.SetValueEx(key, "PATH", 0, winreg.REG_EXPAND_SZ, new)
        except FileNotFoundError:
            winreg.SetValueEx(key, "PATH", 0, winreg.REG_EXPAND_SZ, directory)
        finally:
            winreg.CloseKey(key)
        try:
            import ctypes
            ctypes.windll.user32.SendMessageTimeoutW(
                0xFFFF, 0x001A, 0, "Environment", 0x0002, 5000, None)
        except Exception:
            pass
        return True
    except Exception:
        return False


def _install_windows():
    self_path = Path(__file__).resolve()
    import sysconfig
    scripts = Path(sysconfig.get_path("scripts"))
    if not os.access(str(scripts), os.W_OK):
        scripts = Path(os.environ.get("LOCALAPPDATA", os.path.expanduser("~"))) / "zxgrep-bin"
        scripts.mkdir(parents=True, exist_ok=True)

    target_py = scripts / "zxgrep.py"
    target_cmd = scripts / "zxgrep.cmd"
    target_sh = scripts / "zxgrep"
    shutil.copy2(str(self_path), str(target_py))
    target_cmd.write_text('@python "%~dp0zxgrep.py" %*\n', encoding="ascii")
    target_sh.write_text('#!/bin/sh\nexec python "$(dirname "$0")/zxgrep.py" "$@"\n',
                         encoding="ascii", newline="\n")
    print(f"Installed main program to: {target_py}")
    print(f"Created launcher (cmd):    {target_cmd}")
    print(f"Created launcher (bash):   {target_sh}")

    _install_completion_windows()

    path_dirs = [os.path.normcase(p) for p in os.environ.get("PATH", "").split(os.pathsep) if p]
    if os.path.normcase(str(scripts)) in path_dirs:
        print(f"\n{scripts} is already on your PATH.")
        print("You can now use:  zxgrep <args>")
    else:
        added = _add_to_user_path_windows(str(scripts))
        if added:
            print(f"\nAdded {scripts} to your user PATH.")
            print("Please open a NEW terminal window, then use:  zxgrep <args>")
        else:
            print(f"\nCould not automatically add to PATH.")
            print(f"Please manually add this directory to your user PATH:\n  {scripts}")


def _install_self():
    (_install_windows if sys.platform == "win32" else _install_unix)()


def _clean():
    dirs = list(Path.cwd().glob("zxgrep_*"))
    if not dirs:
        print("No auto-generated output directories found.")
        return
    print("The following directories will be removed:")
    for d in dirs:
        print(f"  {d}")
    if input("Confirm deleting these directories? [y/N] ").strip().lower() in ("y", "yes"):
        for d in dirs:
            if d.is_dir():
                shutil.rmtree(d)
                print(f"Deleted: {d}")
        print("Cleanup done.")
    else:
        print("Cleanup canceled.")


def _enable_ansi():
    if sys.platform != "win32":
        return
    try:
        import ctypes
        k = ctypes.windll.kernel32
        h = k.GetStdHandle(-11)
        m = ctypes.c_ulong()
        k.GetConsoleMode(h, ctypes.byref(m))
        if not (m.value & 0x0004):
            k.SetConsoleMode(h, m.value | 0x0004)
    except Exception:
        pass


# Entry point

def main(argv):
    sys.stdout.reconfigure(encoding="utf-8")
    _enable_ansi()
    args = _parse(argv)

    actions = {
        "help":             lambda: (usage(), 0)[1],
        "install":          lambda: (_install_self(), 0)[1],
        "print-completion": lambda: (print(BASH_COMPLETION_SCRIPT, end=""), 0)[1],
        "clean":            lambda: (_clean(), 0)[1],
    }
    if args["action"] in actions:
        return actions[args["action"]]()

    return 0 if _run(args) else 1


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except KeyboardInterrupt:
        raise SystemExit(130)
    except subprocess.CalledProcessError as e:
        die(f"External command failed, exit code={e.returncode}")