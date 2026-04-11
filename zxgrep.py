#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import re
import sys
import tarfile
import fnmatch
import shutil
import tempfile
import subprocess
import concurrent.futures
from pathlib import Path


PROGRAM = "zxgrep"

RED = "\033[01;31m"
CYAN = "\033[01;36m"
RESET = "\033[0m"

# Boundary definition for exact match:
# Characters before/after the keyword cannot be [A-Za-z0-9_]
LEFT_BOUNDARY = r"(?<![0-9A-Za-z_])"
RIGHT_BOUNDARY = r"(?![0-9A-Za-z_])"


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

    local opts="--help --install --print-bash-completion --clean --file --case-sensitive --exact --regex --or --include --exclude --copy --move --list-files --name-only --color-path --no-color-path --stream --flat -h -s -x -r -l -N -o -O -j --jobs"

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
            --help|-h|--install|--print-bash-completion|--clean|--file|--case-sensitive|-s|--exact|-x|--regex|-r|--or|--copy|--move|--list-files|-l|--name-only|-N|--color-path|--no-color-path|--stream|--flat|-O)
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
     If INPUT is a tar.zst, it will be extracted to /dev/shm first (fallback to /tmp if unavailable).

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
      Install to /usr/local/bin/zxgrep
      and install bash completion.

  20) --clean:
      Clean up all auto-generated output directories in the current directory (prefixed with zxgrep_).
      You will be prompted for confirmation before deletion.

Examples:
  {PROGRAM} archive.tar.zst exec task
  {PROGRAM} ./docs exec task
  {PROGRAM} ./docs/a.txt exec task
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
  {PROGRAM} --clean
""")


def require_cmd(*cmds):
    for cmd in cmds:
        if shutil.which(cmd) is None:
            die(f"Missing required command: {cmd}")


def pick_tmp_root():
    shm = Path("/dev/shm")
    if shm.exists() and os.access(str(shm), os.W_OK):
        return shm
    return Path(os.environ.get("TMPDIR", "/tmp"))


def abs_path(p):
    return Path(os.path.abspath(os.path.expanduser(str(p))))


def sanitize_component(s):
    return re.sub(r"[^\w._+-]", "_", s)


def auto_outdir_name(words):
    return "zxgrep_" + "+".join(sanitize_component(w) for w in words)


def path_is_within(path, parent):
    try:
        return os.path.commonpath([os.path.abspath(path), os.path.abspath(parent)]) == os.path.abspath(parent)
    except ValueError:
        return False


def is_probably_text_file(path):
    try:
        with open(path, "rb") as f:
            chunk = f.read(8192)
        return b"\x00" not in chunk
    except Exception:
        return False


def pretty_local_path(path):
    path = abs_path(path)
    cwd = abs_path(".")
    try:
        rel = path.relative_to(cwd)
        rel_s = rel.as_posix()
        if rel_s == ".":
            return "."
        return "./" + rel_s
    except ValueError:
        return path.as_posix()


def maybe_color_path_text(text, color_path, is_tty):
    if color_path and is_tty:
        return f"{CYAN}{text}{RESET}"
    return text


def make_location_label(display_path, lineno, colno, color_path, is_tty):
    text = f"{display_path}:{lineno}:{colno}"
    return maybe_color_path_text(text, color_path, is_tty)


def check_file_filters(rel_path, filters):
    if not filters:
        return True
    basename = Path(rel_path).name
    for pat in filters.get("exclude", []):
        if fnmatch.fnmatch(basename, pat):
            return False
        if fnmatch.fnmatch(rel_path, pat):
            return False
    includes = filters.get("include", [])
    if includes:
        return any(fnmatch.fnmatch(basename, pat) for pat in includes)
    return True


def detect_input_kind(input_path):
    p = abs_path(input_path)
    if not p.exists():
        die(f"Input does not exist: {p}")
    if p.is_dir():
        return {"kind": "dir", "path": p}
    if p.is_file():
        if p.name.endswith(".tar.zst"):
            return {"kind": "archive", "path": p}
        return {"kind": "file", "path": p}
    die(f"Unsupported input type: {p}")


def extract_archive(archive, dest):
    require_cmd("tar", "zstd")
    subprocess.run(
        ["tar", "-I", "zstd -T0", "-xf", str(archive), "-C", str(dest)],
        check=True
    )


def iter_dir_files(root, exclude_dir=None, filters=None):
    root = abs_path(root)
    exclude_abs = abs_path(exclude_dir) if exclude_dir is not None else None

    for current, dirnames, filenames in os.walk(str(root), followlinks=False):
        if exclude_abs is not None:
            new_dirnames = []
            for d in dirnames:
                full = os.path.join(current, d)
                if path_is_within(full, str(exclude_abs)):
                    continue
                new_dirnames.append(d)
            dirnames[:] = sorted(new_dirnames)
        else:
            dirnames[:] = sorted(dirnames)

        filenames.sort()

        for name in filenames:
            p = Path(current) / name
            if exclude_abs is not None and path_is_within(str(p), str(exclude_abs)):
                continue
            if p.is_symlink():
                continue
            if not p.is_file():
                continue
            rel = p.relative_to(root).as_posix()
            if not check_file_filters(rel, filters):
                continue
            yield {
                "rel": rel,
                "path": str(p),
                "display_path": pretty_local_path(p),
            }


def iter_archive_files(extracted_root, filters=None):
    extracted_root = abs_path(extracted_root)
    for current, dirnames, filenames in os.walk(str(extracted_root), followlinks=False):
        dirnames[:] = sorted(dirnames)
        filenames.sort()

        for name in filenames:
            p = Path(current) / name
            if p.is_symlink():
                continue
            if not p.is_file():
                continue
            rel = p.relative_to(extracted_root).as_posix()
            if not check_file_filters(rel, filters):
                continue
            yield {
                "rel": rel,
                "path": str(p),
                "display_path": rel,
            }


def iter_single_file(file_path):
    p = abs_path(file_path)
    if p.is_file():
        yield {
            "rel": p.name,
            "path": str(p),
            "display_path": pretty_local_path(p),
        }


def compile_pattern(word, mode, case_sensitive):
    flags = 0 if case_sensitive else re.IGNORECASE

    if mode == "substr":
        expr = re.escape(word)
    elif mode == "exact":
        expr = LEFT_BOUNDARY + re.escape(word) + RIGHT_BOUNDARY
    elif mode == "regex":
        expr = word
    else:
        die(f"Internal error: unknown match mode {mode}")

    try:
        pat = re.compile(expr, flags)
    except re.error as ex:
        die(f"Regex compilation failed: {word!r}: {ex}")

    if mode == "regex":
        m = pat.search("")
        if m is not None and m.start() == m.end():
            die(f"Regex is not allowed to match empty string: {word!r}")

    return pat


def build_patterns(words, mode, case_sensitive):
    return [compile_pattern(w, mode, case_sensitive) for w in words]


def build_highlight_pattern(words, mode, case_sensitive):
    flags = 0 if case_sensitive else re.IGNORECASE

    if mode == "substr":
        uniq = list(dict.fromkeys(words))
        uniq.sort(key=len, reverse=True)
        expr = "|".join(re.escape(w) for w in uniq)
    elif mode == "exact":
        uniq = list(dict.fromkeys(words))
        uniq.sort(key=len, reverse=True)
        body = "|".join(re.escape(w) for w in uniq)
        expr = LEFT_BOUNDARY + f"(?:{body})" + RIGHT_BOUNDARY
    elif mode == "regex":
        parts = [f"(?:{w})" for w in words]
        expr = "|".join(parts)
    else:
        die(f"Internal error: unknown match mode {mode}")

    try:
        pat = re.compile(expr, flags)
    except re.error as ex:
        die(f"Highlight regex compilation failed: {ex}")

    return pat


def colorize_line(line, any_pattern, colorize=True):
    text = line.rstrip("\r\n")
    if colorize:
        return any_pattern.sub(lambda m: f"{RED}{m.group(0)}{RESET}", text)
    else:
        return text


def first_match_column(line, any_pattern):
    m = any_pattern.search(line)
    if m is None:
        return 1
    return m.start() + 1


def remove_path_if_exists(path):
    path = abs_path(path)
    if path.exists() or path.is_symlink():
        if path.is_dir() and not path.is_symlink():
            shutil.rmtree(path)
        else:
            path.unlink()


def safe_move(src, dst):
    src = abs_path(src)
    dst = abs_path(dst)

    if os.path.abspath(src) == os.path.abspath(dst):
        return

    dst.parent.mkdir(parents=True, exist_ok=True)
    remove_path_if_exists(dst)
    shutil.move(str(src), str(dst))


def safe_copy(src, dst):
    src = abs_path(src)
    dst = abs_path(dst)

    if os.path.abspath(src) == os.path.abspath(dst):
        return

    dst.parent.mkdir(parents=True, exist_ok=True)
    remove_path_if_exists(dst)
    shutil.copy2(str(src), str(dst))


def safe_transfer(src, dst, transfer_mode):
    if transfer_mode == "copy":
        safe_copy(src, dst)
    elif transfer_mode == "move":
        safe_move(src, dst)
    else:
        die(f"Internal error: unknown output mode {transfer_mode}")


def resolve_flat_target(outdir, rel_path):
    basename = Path(rel_path).name
    target = outdir / basename

    if not target.exists():
        return target

    stem = Path(basename).stem
    suffix = Path(basename).suffix
    n = 1
    while True:
        new_name = f"{stem}.conflict-{n}{suffix}"
        target = outdir / new_name
        if not target.exists():
            return target
        n += 1


def build_source_items(source_info, extracted_root=None, exclude_dir=None, filters=None):
    kind = source_info["kind"]

    if kind == "archive":
        if extracted_root is None:
            die("Internal error: archive mode missing extracted_root")
        return list(iter_archive_files(extracted_root, filters=filters))

    if kind == "dir":
        return list(iter_dir_files(source_info["path"], exclude_dir=exclude_dir, filters=filters))

    if kind == "file":
        return list(iter_single_file(source_info["path"]))

    die(f"Internal error: unknown input kind {kind}")


def worker_search_file(args):
    item, all_patterns, any_pattern, opts = args

    path = item["path"]
    file_mode = opts["file_mode"]
    list_files = opts["list_files"]
    name_only = opts["name_only"]
    or_mode = opts["or_mode"]

    if name_only:
        name = Path(item["rel"]).name
        if or_mode:
            matched = any(p.search(name) for p in all_patterns)
        else:
            matched = all(p.search(name) for p in all_patterns)
        if matched:
            return (item, [])
        return None

    if not is_probably_text_file(path):
        return None

    try:
        if file_mode:
            found_patterns = set()
            lines_cache = []

            with open(path, "r", encoding="utf-8", errors="replace", newline="") as f:
                for line in f:
                    lines_cache.append(line)
                    for idx, pat in enumerate(all_patterns):
                        if pat.search(line):
                            found_patterns.add(idx)

            if or_mode:
                if not found_patterns:
                    return None
            else:
                if found_patterns != set(range(len(all_patterns))):
                    return None

            if list_files:
                return (item, [])

            matches = []
            for lineno, line in enumerate(lines_cache, start=1):
                if any_pattern.search(line):
                    colno = first_match_column(line, any_pattern)
                    matches.append((lineno, colno, line))
            return (item, matches)

        else:
            matches = []
            with open(path, "r", encoding="utf-8", errors="replace", newline="") as f:
                for lineno, line in enumerate(f, start=1):
                    if or_mode:
                        matched = any(p.search(line) for p in all_patterns)
                    else:
                        matched = all(p.search(line) for p in all_patterns)

                    if matched:
                        if list_files:
                            return (item, [])
                        colno = first_match_column(line, any_pattern)
                        matches.append((lineno, colno, line))

            if matches:
                return (item, matches)
            return None

    except Exception:
        return None


def handle_result(item, matches, outdir, transfer_mode, color_path, is_tty,
                  list_files, name_only, any_pattern, flat):
    if outdir is not None:
        if flat:
            target = resolve_flat_target(outdir, item["rel"])
        else:
            target = outdir / item["rel"]
        safe_transfer(item["path"], target, transfer_mode)
        display = pretty_local_path(target)
    else:
        display = item["display_path"]

    if list_files or name_only:
        text = maybe_color_path_text(display, color_path, is_tty)
        sys.stdout.write(text + "\n")
        sys.stdout.flush()
    else:
        for lineno, colno, line in matches:
            prefix = make_location_label(display, lineno, colno, color_path, is_tty)
            colored_line = colorize_line(line, any_pattern, colorize=is_tty)
            sys.stdout.write(f"{prefix}: {colored_line}\n")
            sys.stdout.flush()


def choose_completion_target():
    candidates = [
        Path("/usr/share/bash-completion/completions"),
        Path("/usr/local/share/bash-completion/completions"),
        Path("/etc/bash_completion.d"),
    ]
    for d in candidates:
        if d.exists() and d.is_dir():
            return d / PROGRAM
    return Path("/usr/local/share/bash-completion/completions") / PROGRAM


def install_file(src, dst, mode, use_sudo):
    require_cmd("install")
    if use_sudo:
        require_cmd("sudo")
        subprocess.run(["sudo", "mkdir", "-p", str(dst.parent)], check=True)
        subprocess.run(["sudo", "install", "-m", f"{mode:o}", str(src), str(dst)], check=True)
    else:
        dst.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(["install", "-m", f"{mode:o}", str(src), str(dst)], check=True)


def install_self():
    self_path = Path(__file__).resolve()
    bin_target = Path("/usr/local/bin") / PROGRAM
    comp_target = choose_completion_target()
    use_sudo = (os.geteuid() != 0)

    tmp_completion = None
    try:
        fd, tmp_name = tempfile.mkstemp(prefix="zxgrep_completion_", text=True)
        os.close(fd)
        tmp_completion = Path(tmp_name)
        tmp_completion.write_text(BASH_COMPLETION_SCRIPT, encoding="utf-8")

        install_file(self_path, bin_target, 0o755, use_sudo)
        install_file(tmp_completion, comp_target, 0o644, use_sudo)

    finally:
        if tmp_completion and tmp_completion.exists():
            try:
                tmp_completion.unlink()
            except Exception:
                pass

    print(f"Installed main program to: {bin_target}")
    print(f"Installed Bash completion to: {comp_target}")
    print("If completion still doesn't work in the current shell, open a new Bash or run:")
    print(f"  source {comp_target}")


def print_bash_completion():
    print(BASH_COMPLETION_SCRIPT, end="")


def clean_auto_outdirs():
    current_dir = Path.cwd()
    pattern = "zxgrep_*"
    dirs = list(current_dir.glob(pattern))
    if not dirs:
        print("No auto-generated output directories found.")
        return

    print("The following directories will be removed:")
    for d in dirs:
        print(f"  {d}")

    response = input("Confirm deleting these directories? [y/N] ").strip().lower()
    if response in ('y', 'yes'):
        for d in dirs:
            if d.is_dir():
                shutil.rmtree(d)
                print(f"Deleted: {d}")
        print("Cleanup done.")
    else:
        print("Cleanup canceled.")


def parse_args(argv):
    if not argv:
        usage()
        raise SystemExit(1)

    if len(argv) == 1 and argv[0] in ("-h", "--help"):
        usage()
        raise SystemExit(0)

    if len(argv) == 1 and argv[0] == "--install":
        return {"action": "install"}

    if len(argv) == 1 and argv[0] == "--print-bash-completion":
        return {"action": "print-completion"}

    if len(argv) == 1 and argv[0] == "--clean":
        return {"action": "clean"}

    input_path = None
    words = []
    file_mode = False
    outdir = None
    auto_out = False
    case_sensitive = False
    list_files = False
    name_only = False
    color_path = True
    mode = "substr"           # substr / exact / regex
    transfer_mode = "copy"    # copy / move
    transfer_mode_set = False
    jobs = None
    or_mode = False
    includes = []
    excludes = []
    stream = False
    flat = False

    stop_opts = False
    i = 0

    while i < len(argv):
        arg = argv[i]

        if not stop_opts and arg == "--":
            stop_opts = True
            i += 1
            continue

        if not stop_opts:
            if arg in ("-h", "--help"):
                usage()
                raise SystemExit(0)
            elif arg == "--install":
                die("--install cannot be used together with search arguments")
            elif arg == "--print-bash-completion":
                die("--print-bash-completion cannot be used together with search arguments")
            elif arg == "--clean":
                die("--clean cannot be used together with search arguments")
            elif arg == "--file":
                file_mode = True
                i += 1
                continue
            elif arg in ("-s", "--case-sensitive"):
                case_sensitive = True
                i += 1
                continue
            elif arg in ("-x", "--exact"):
                if mode == "regex":
                    die("--exact and --regex cannot be used together")
                mode = "exact"
                i += 1
                continue
            elif arg in ("-r", "--regex"):
                if mode == "exact":
                    die("--exact and --regex cannot be used together")
                mode = "regex"
                i += 1
                continue
            elif arg == "--or":
                or_mode = True
                i += 1
                continue
            elif arg == "--include":
                i += 1
                if i >= len(argv):
                    die("--include requires a glob pattern")
                includes.append(argv[i])
                i += 1
                continue
            elif arg == "--exclude":
                i += 1
                if i >= len(argv):
                    die("--exclude requires a glob pattern")
                excludes.append(argv[i])
                i += 1
                continue
            elif arg in ("-l", "--list-files"):
                list_files = True
                i += 1
                continue
            elif arg in ("-N", "--name-only"):
                name_only = True
                file_mode = True
                list_files = True
                i += 1
                continue
            elif arg == "--color-path":
                color_path = True
                i += 1
                continue
            elif arg == "--no-color-path":
                color_path = False
                i += 1
                continue
            elif arg == "--copy":
                if transfer_mode_set and transfer_mode != "copy":
                    die("--copy and --move cannot be used together")
                transfer_mode = "copy"
                transfer_mode_set = True
                i += 1
                continue
            elif arg == "--move":
                if transfer_mode_set and transfer_mode != "move":
                    die("--copy and --move cannot be used together")
                transfer_mode = "move"
                transfer_mode_set = True
                i += 1
                continue
            elif arg == "-o":
                i += 1
                if i >= len(argv):
                    die("-o requires an output directory")
                if auto_out:
                    die("-o and -O cannot be used together")
                outdir = abs_path(argv[i])
                i += 1
                continue
            elif arg == "-O":
                if outdir is not None:
                    die("-o and -O cannot be used together")
                auto_out = True
                i += 1
                continue
            elif arg in ("-j", "--jobs"):
                i += 1
                if i >= len(argv):
                    die("-j/--jobs requires a process count")
                try:
                    jobs = int(argv[i])
                    if jobs < 1:
                        die("Process count must be a positive integer")
                except ValueError:
                    die(f"Invalid process count: {argv[i]}")
                i += 1
                continue
            elif arg == "--stream":
                stream = True
                i += 1
                continue
            elif arg == "--flat":
                flat = True
                i += 1
                continue
            elif arg.startswith("-"):
                die(f"Unsupported option: {arg}")

        if input_path is None:
            input_path = arg
        else:
            words.append(arg)
        i += 1

    if input_path is None:
        die("Missing INPUT")
    if not words:
        die("At least one keyword/expression is required")
    if any(w == "" for w in words):
        die("Keyword/expression cannot be empty")

    if auto_out:
        outdir = abs_path(auto_outdir_name(words))

    if flat and outdir is None:
        die("--flat can only be used with -o or -O")

    if transfer_mode_set and outdir is None:
        die("--copy/--move can only be used with -o or -O")

    if jobs is None:
        jobs = os.cpu_count() or 4

    filters = None
    if includes or excludes:
        filters = {"include": includes, "exclude": excludes}

    return {
        "action": "search",
        "input_path": abs_path(input_path),
        "words": words,
        "file_mode": file_mode,
        "outdir": outdir,
        "case_sensitive": case_sensitive,
        "mode": mode,
        "or_mode": or_mode,
        "list_files": list_files,
        "name_only": name_only,
        "transfer_mode": transfer_mode,
        "color_path": color_path,
        "jobs": jobs,
        "filters": filters,
        "stream": stream,
        "flat": flat,
    }


def run_search(args):
    input_path = args["input_path"]
    words = args["words"]
    file_mode = args["file_mode"]
    outdir = args["outdir"]
    case_sensitive = args["case_sensitive"]
    mode = args["mode"]
    or_mode = args["or_mode"]
    list_files = args["list_files"]
    name_only = args["name_only"]
    transfer_mode = args["transfer_mode"]
    color_path = args["color_path"]
    jobs = args["jobs"]
    filters = args["filters"]
    stream = args["stream"]
    flat = args["flat"]

    source_info = detect_input_kind(input_path)

    all_patterns = build_patterns(words, mode=mode, case_sensitive=case_sensitive)
    any_pattern = build_highlight_pattern(words, mode=mode, case_sensitive=case_sensitive)

    is_tty = sys.stdout.isatty()

    opts = {
        "file_mode": file_mode,
        "list_files": list_files,
        "name_only": name_only,
        "or_mode": or_mode,
    }

    if outdir is not None:
        outdir = abs_path(outdir)
        outdir.mkdir(parents=True, exist_ok=True)

    found = False
    matched_count = 0

    def on_result(result):
        nonlocal found, matched_count
        if result is None:
            return
        found = True
        matched_count += 1
        handle_result(result[0], result[1], outdir, transfer_mode,
                      color_path, is_tty, list_files, name_only, any_pattern, flat)

    temp_root = None
    stream_tmp = None
    stream_proc = None

    try:
        if source_info["kind"] == "archive" and stream:
            require_cmd("zstd")
            stream_proc = subprocess.Popen(
                ["zstd", "-d", "-T0", str(source_info["path"]), "-c"],
                stdout=subprocess.PIPE,
            )
            stream_tmp = Path(tempfile.mkdtemp(prefix="zxgrep_stream."))
            tf = tarfile.open(fileobj=stream_proc.stdout, mode="r|")
            for member in tf:
                if not member.isfile():
                    continue
                rel = member.name
                if rel.startswith("./"):
                    rel = rel[2:]
                if not check_file_filters(rel, filters):
                    continue
                ef = tf.extractfile(member)
                if ef is None:
                    continue
                tmp_path = stream_tmp / "current"
                with open(str(tmp_path), "wb") as out_f:
                    shutil.copyfileobj(ef, out_f)
                item = {"rel": rel, "path": str(tmp_path), "display_path": rel}
                on_result(worker_search_file((item, all_patterns, any_pattern, opts)))
                try:
                    tmp_path.unlink()
                except FileNotFoundError:
                    pass
            tf.close()
        else:
            extracted_root = None
            if source_info["kind"] == "archive":
                temp_root = Path(tempfile.mkdtemp(prefix="zxgrep.", dir=str(pick_tmp_root())))
                extract_archive(source_info["path"], temp_root)
                extracted_root = temp_root

            exclude_dir = None
            if source_info["kind"] == "dir" and outdir is not None:
                if path_is_within(str(outdir), str(source_info["path"])):
                    exclude_dir = outdir

            items = build_source_items(
                source_info,
                extracted_root=extracted_root,
                exclude_dir=exclude_dir,
                filters=filters,
            )

            if not items:
                return False

            task_args = [
                (item, all_patterns, any_pattern, opts)
                for item in items
            ]

            with concurrent.futures.ProcessPoolExecutor(max_workers=jobs) as executor:
                future_to_item = {
                    executor.submit(worker_search_file, arg): arg[0]
                    for arg in task_args
                }
                for future in concurrent.futures.as_completed(future_to_item):
                    try:
                        result = future.result()
                    except Exception:
                        continue
                    on_result(result)

        if outdir is not None and matched_count > 0:
            action_text = "copied" if transfer_mode == "copy" else "moved"
            eprint(f"Matched files have been {action_text} to: {pretty_local_path(outdir)}")

        return found

    finally:
        if temp_root is not None:
            shutil.rmtree(temp_root, ignore_errors=True)
        if stream_tmp is not None:
            shutil.rmtree(stream_tmp, ignore_errors=True)
        if stream_proc is not None:
            stream_proc.terminate()
            stream_proc.wait()


def main(argv):
    args = parse_args(argv)

    if args["action"] == "install":
        install_self()
        return 0

    if args["action"] == "print-completion":
        print_bash_completion()
        return 0

    if args["action"] == "clean":
        clean_auto_outdirs()
        return 0

    found = run_search(args)
    return 0 if found else 1


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except KeyboardInterrupt:
        raise SystemExit(130)
    except subprocess.CalledProcessError as e:
        die(f"External command failed, exit code={e.returncode}")