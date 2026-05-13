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
DIM = "\033[02;36m"
RESET = "\033[0m"

# Boundary definition for exact match:
# Characters before/after the keyword cannot be [A-Za-z0-9_]
LEFT_BOUNDARY = r"(?<![0-9A-Za-z_])"
RIGHT_BOUNDARY = r"(?![0-9A-Za-z_])"

SPECIAL_EXTS = ('.pdf', '.epub', '.mobi', '.azw3')

MARKUP_EXTS = ('.md', '.markdown', '.mdown', '.mkdn', '.mkd', '.mdwn', '.rmd', '.qmd', '.html', '.htm')

ARCHIVE_EXTS = ('.tar.zst', '.tar.gz', '.tgz', '.tar.bz2', '.tbz2', '.tar.xz', '.txz', '.tar', '.zip')

def is_archive(path):
    return any(path.endswith(e) for e in ARCHIVE_EXTS)


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

    local opts="--help --install --print-bash-completion --clean --file --case-sensitive --exact --regex --or --ordered --window --include --exclude --copy --move --list-files --name-only --color-path --no-color-path --stream --flat --ugrep --strip --max-count -h -s -x -r -l -N -o -O -j -w -m --jobs"

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
            --help|-h|--install|--print-bash-completion|--clean|--file|--case-sensitive|-s|--exact|-x|--regex|-r|--or|--ordered|--copy|--move|--list-files|-l|--name-only|-N|--color-path|--no-color-path|--stream|--flat|--ugrep|--strip|-O)
                ;;
            -o|-j|--jobs|--include|--exclude|-m|--max-count|-w|--window|-A|-B|-C)
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
    local opts=(--help -h --install --print-bash-completion --clean --file --case-sensitive -s --exact -x --regex -r --or --ordered --window --include --exclude --copy --move --list-files -l --name-only -N --color-path --no-color-path --stream --flat --ugrep -o -O -j -w --jobs)
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
  {PROGRAM} INPUT WORD1 [WORD2 ...] --ordered
  {PROGRAM} INPUT WORD1 [WORD2 ...] -w 3
  {PROGRAM} INPUT WORD1 [WORD2 ...] --include '*.py' --exclude 'test_*'
  {PROGRAM} INPUT WORD1 [WORD2 ...] -l
  {PROGRAM} INPUT WORD1 [WORD2 ...] -m 5
  {PROGRAM} INPUT WORD1 [WORD2 ...] -C 2
  {PROGRAM} INPUT WORD1 [WORD2 ...] -N
  {PROGRAM} INPUT WORD1 [WORD2 ...] -j 8
  {PROGRAM} INPUT WORD1 [WORD2 ...] --stream
  {PROGRAM} INPUT WORD1 [WORD2 ...] -O --flat
  {PROGRAM} INPUT WORD1 [WORD2 ...] --ugrep
  {PROGRAM} INPUT WORD1 [WORD2 ...] --strip
  {PROGRAM} --install
  {PROGRAM} --print-bash-completion
  {PROGRAM} --clean

~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~ Input & File Formats ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

  1) INPUT is auto-detected as:
     - An archive (.tar.zst, .tar.gz/.tgz, .tar.bz2/.tbz2, .tar.xz/.txz, .tar, .zip)
     - A directory (recursively process text files inside)
     - A single text file
     .tar.zst requires the 'zstd' command or the 'zstandard' Python package:
       Linux:   sudo apt install zstd  (or: pip install zstandard)
       Windows: choco install zstd     (or: pip install zstandard)
     Other archive formats use the Python standard library (no external dependency).
     Archives are extracted to a temporary directory first
     (prefers shared memory on Linux if available, otherwise system temp).

  2) Text file detection:
     For directory/single-file input, obvious binary files are skipped (simple NUL-byte check).

  3) PDF support (.pdf):
     Files ending in .pdf are automatically extracted and searched.
     Requires the 'pdftotext' command (part of Poppler):
       Linux:   sudo apt install poppler-utils
       Windows: choco install poppler (or scoop install poppler)
     If 'pdftotext' is not installed, PDF files are silently skipped.

  4) eBook support (.epub / .mobi / .azw3):
     - .epub files are parsed natively using the Python standard library (no external
       dependencies). HTML/XHTML content inside the EPUB archive is extracted as plain text.
     - .mobi and .azw3 files require the 'ebook-convert' command (part of Calibre):
         Linux:   sudo apt install calibre
         Windows: choco install calibre
       If 'ebook-convert' is not installed, MOBI/AZW3 files are silently skipped.

  5) --strip:
     Strip Markup syntax from Markup files (.md, .html, etc.)
     before searching, keeping only plain text content.
     Non-markup files are left untouched.
     Removes: Markdown/HTML formatting, HTML tags, <script>/<style> blocks,
     HTML comments, while preserving body text.
     All existing features (including -o, -x, -r, --file, etc.) are supported.
     When combined with --ugrep, --strip forces a fallback to the Python engine
     because ugrep cannot search pre-stripped content natively.
     Example:
       {PROGRAM} ./docs exec --strip
       {PROGRAM} archive.tar.zst exec --strip -x -s

~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~ Matching ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

  6) Default mode:
     Search by "line".
     The same line must contain all keywords (AND mode).

  7) --file mode:
     Search by "file".
     The same file must contain all keywords; the keywords do not need to be on the same line.
     By default, output lines that contain any keyword/expression in those files, with highlighting.

  8) -N / --name-only:
     Search only on the "filename itself" (basename), not file contents.
     This mode automatically operates at file level and only outputs matched file paths.
     Also supports -o/-O, --copy, --move.
     Example:
       {PROGRAM} ./docs report -N
       {PROGRAM} ./docs 'report.*2024' -N -r

  9) Default matching:
     Non-exact + case-insensitive
     i.e., normal substring matching.
     Example keyword exec:
       Can match: exec, execution, EXEC, my_exec_call

  10) -x / --exact:
     Enable exact matching.
     Exact match is defined as:
       characters before/after the keyword cannot be English letters / digits / underscore
     Example keyword exec:
       Matches: " exec ", "(exec)", "exec;"
       Does not match: "execution", "my_exec_var", "exec123"

  11) -r / --regex:
      Enable regex matching.
      Each WORD is treated as a regular expression.
      Multiple keywords are still supported.
      Matching is still line-based; multi-line regex across lines is not supported.

  12) -s / --case-sensitive:
      Enable case-sensitive matching.

  13) --or:
      Use OR logic to connect multiple keywords (default is AND).
      - Default (AND): must contain all keywords
      - OR mode: match if any keyword is present
      Example:
        {PROGRAM} ./docs exec task        # AND: must contain both exec and task
        {PROGRAM} ./docs exec task --or   # OR:  contains exec or task

  14) --ordered:
      Require keywords to appear in the specified order.
      - Line mode: each keyword must be found after the previous one on the same line
      - --file mode: each keyword must appear on a line at or after the previous keyword's line
      Example:
        {PROGRAM} ./docs exec task --ordered        # line: "exec" before "task"
        {PROGRAM} ./docs exec task --ordered --file # file: "exec" before "task" across lines

  15) --window N / -w N:
      Search across N consecutive lines at a time.
      All keywords must appear somewhere in the same N-line span.
      Combine with --ordered to require them in order within the span.
      Cannot be used with --file, --or, --name-only.
      Example:
        {PROGRAM} ./docs exec task -w 3
        {PROGRAM} ./docs exec task -w 3 --ordered

~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~ Filtering ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

  16) --include GLOB:
      Only search files whose basename matches the specified glob pattern.
      Matching is based on the file basename (without directories).
      Can be specified multiple times to add multiple patterns (any match is accepted).
      Example:
        {PROGRAM} ./docs exec --include '*.py'
        {PROGRAM} ./docs exec --include '*.py' --include '*.js'

  17) --exclude GLOB:
      Exclude files whose names match the specified glob pattern.
      Matches against basename and relative path (either match excludes).
      Can be specified multiple times to add multiple patterns.
      Example:
        {PROGRAM} ./docs exec --exclude '*.log'
        {PROGRAM} ./docs exec --exclude 'node_modules' --exclude '*.min.js'

~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~ Output ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

  18) -l / --list-files:
      Only list matched file paths, do not output matched lines.
      - In default mode: list files that have at least one line matching all keywords
      - In --file mode: list files that contain all keywords

  19) -m / --max-count N:
      Stop after N matches per file.
      In line mode: output at most N matching lines per file.
      In --file mode: output at most N lines (among those containing any keyword).
      Has no effect with -l or -N.
      Example:
        {PROGRAM} ./docs exec -m 3
        {PROGRAM} ./docs exec task --file -m 5

  20) -A / -B / -C N:
      Show N lines of context around each match.
      -A N: show N lines after each match.
      -B N: show N lines before each match.
      -C N: show N lines before and after each match (equivalent to -A N -B N).
      Context lines are displayed with column 0 (dimmed path), match lines
      keep the usual highlighting. Overlapping windows from adjacent matches
      are merged automatically.
      Has no effect with -l or -N.
      Example:
        {PROGRAM} ./docs exec -C 2
        {PROGRAM} ./docs exec -A 3 -B 1

  21) Default output includes line and column numbers:
      Like:
        path/to/file.txt:12:8: matched line

  22) Path coloring:
      Paths are colored by default.
      To avoid affecting VSCode's path:line:col recognition, you may disable path coloring.
      Disable:
        --no-color-path
      Explicitly enable (default behavior):
        --color-path

  23) -o OUTDIR / --outdir OUTDIR  /  -O / --auto-outdir:
      Output matched files into a target directory (does not change matching behavior).
      Default behavior is "copy".
      To switch to move, add:
        --move

      Related options:
        --copy   explicitly copy (default)
        --move   move instead

      To avoid name collisions, the relative directory structure is preserved.
      - For archives: preserve paths inside the archive
      - For a directory: preserve paths relative to the input directory
      - For a single file: output as same filename under the target directory

  24) --flat:
      Flatten output directory structure (only effective with -o or -O).
      Instead of preserving the original directory hierarchy, all matched files
      are placed directly in the target directory (single level).
      If multiple files have the same name, conflicts are resolved by appending
      .conflict-N before the file extension.
      Example:
        {PROGRAM} ./docs exec task -O --flat
        # Results in: zxgrep_exec+task/file1.txt, zxgrep_exec+task/file1.conflict-1.txt

~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~ Performance ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

  25) -j / --jobs:
      Specify number of parallel worker processes.
      Default uses CPU core count.
      Search uses multi-process parallelism; output is streamed in real time (order not guaranteed).
      Example:
        {PROGRAM} ./docs exec task -j 8
        {PROGRAM} archive.tar.zst exec -j 4

  26) --stream:
      Stream processing for .tar.zst archives only.
      Instead of extracting the entire archive to a temporary directory,
      process files one by one directly from the tar stream.
      Avoids high temporary disk usage for large archives.
      For other archive formats, directories, or single files, this flag has no effect.
      Note: -j/--jobs is ignored in stream mode (processing is sequential).

  27) --ugrep:
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
        - --strip mode
        - --ordered mode
        - --window mode
        - -A / -B / -C modes (context lines)
        - PDF / EPUB / MOBI / AZW3 files (handled by Python, then merged)

~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~ Commands ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

   28) --install:
      Install to /usr/local/bin/zxgrep and bash completion (Unix).
      On Windows, creates zxgrep.cmd launcher and adds to user PATH.

   29) --clean:
      Clean up all auto-generated output directories in the current directory (prefixed with zxgrep_).
      You will be prompted for confirmation before deletion.

~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~ Exit Codes ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

   30) Exit codes:
       0  match found
       1  no match found
       2  error (invalid arguments, missing dependencies, etc.)

Examples:
  {PROGRAM} archive.tar.zst exec task
  {PROGRAM} archive.tar.gz exec task
  {PROGRAM} archive.tgz exec task
  {PROGRAM} archive.tar.bz2 exec task
  {PROGRAM} archive.tar.xz exec task
  {PROGRAM} archive.zip exec task
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
  {PROGRAM} ./docs exec task --ordered
  {PROGRAM} ./docs exec task --ordered --file
  {PROGRAM} ./docs exec task -w 3
  {PROGRAM} ./docs exec task -w 3 --ordered
  {PROGRAM} ./docs exec --include '*.py'
  {PROGRAM} ./docs exec --include '*.py' --include '*.js' --exclude 'test_*'
  {PROGRAM} ./docs exec task -l
  {PROGRAM} ./docs exec task -m 5
  {PROGRAM} ./docs exec task -C 2
  {PROGRAM} ./docs exec task -O --move
  {PROGRAM} ./docs exec task -O --flat
  {PROGRAM} ./docs report -N
  {PROGRAM} ./docs 'report.*2024' -N -r
  {PROGRAM} ./docs exec task -j 4
  {PROGRAM} archive.tar.zst exec task --stream
  {PROGRAM} ./docs exec task --ugrep
  {PROGRAM} ./docs exec task --ugrep --file --or
  {PROGRAM} ./docs exec task --strip
  {PROGRAM} --clean
""")


# Options registry: (long, short, takes_value, accumulative, default, conflicts)

OPTIONS = [
    ("--help",                  "-h", False, False, False,  None),
    ("--install",               None, False, False, False,  None),
    ("--print-bash-completion", None, False, False, False,  None),
    ("--clean",                 None, False, False, False,  None),
    ("--file",                  None, False, False, False,  None),
    ("--case-sensitive",        "-s", False, False, False,  None),
    ("--exact",                 "-x", False, False, False,  ("--regex",)),
    ("--regex",                 "-r", False, False, False,  ("--exact",)),
    ("--or",                    None, False, False, False,  ("--ordered",)),
    ("--ordered",               None, False, False, False,  ("--or",)),
    ("--window",                "-w", True,  False, None,   ("--file", "--or", "--name-only")),
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
    ("--strip",                 None, False, False, False,  None),
    ("--max-count",             "-m", True,  False, None,   None),
    ("-A",                      None, True,  False, 0,      None),
    ("-B",                      None, True,  False, 0,      None),
    ("-C",                      None, True,  False, 0,      None),
]

OPT_BY_FLAG = {}
for o in OPTIONS:
    OPT_BY_FLAG[o[0]] = o
    if o[1]:
        OPT_BY_FLAG[o[1]] = o

STANDALONE = {
    "-h": "help", "--help": "help",
    "--install": "install",
    "--print-bash-completion": "print-completion",
    "--clean": "clean",
}

ACTION_FLAGS = ("--install", "--print-bash-completion", "--clean")

DERIVATIONS = [
    # (trigger, target, value)
    ("--name-only", "--file", True),
    ("--name-only", "--list-files", True),
]

REQUIRES_OUTDIR = ("--flat", "--copy", "--move")


def parse_jobs(raw):
    try:
        j = int(raw)
        if j < 1: raise ValueError
        return j
    except (ValueError, TypeError):
        die(f"Invalid process count: {raw}")


def parse(argv):
    if not argv:
        usage(); raise SystemExit(1)

    if len(argv) == 1 and argv[0] in STANDALONE:
        return {"action": STANDALONE[argv[0]]}

    args = {}
    for o in OPTIONS:
        long, _, _, accum, default, _ = o
        args[long] = list(default) if accum else default

    words, input_path, stop = [], None, False
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg == "--":
            stop = True; i += 1; continue
        if not stop and arg in OPT_BY_FLAG:
            opt = OPT_BY_FLAG[arg]
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
    conflict = next((f for f in ACTION_FLAGS if args[f]), None)
    if conflict:
        die(f"{conflict} cannot be used together with search arguments")
    if input_path is None:
        die("Missing INPUT")
    if not words:
        die("At least one keyword/expression is required")
    if any(w == "" for w in words):
        die("Keyword/expression cannot be empty")

    for o in OPTIONS:
        long, _, _, _, _, conflicts = o
        if conflicts and args[long]:
            for c in conflicts:
                if args[c]:
                    die(f"{long} and {c} cannot be used together")

    for trigger, target, value in DERIVATIONS:
        if args[trigger]:
            args[target] = value

    if args["--no-color-path"]:
        args["--color-path"] = False

    if args["--auto-outdir"]:
        safe = [re.sub(r"[^\w._+-]", "_", w) for w in words]
        args["--outdir"] = os.path.abspath(f"zxgrep_{'+'.join(safe)}")

    outdir = Path(os.path.abspath(os.path.expanduser(str(args["--outdir"])))) if args["--outdir"] else None

    for flag in REQUIRES_OUTDIR:
        if args[flag] and outdir is None:
            die(f"{flag} can only be used with -o or -O")

    jobs = parse_jobs(args["--jobs"]) if args["--jobs"] is not None else (os.cpu_count() or 4)

    max_count = parse_jobs(args["--max-count"]) if args["--max-count"] is not None else None

    window = parse_jobs(args["--window"]) if args["--window"] is not None else 0

    if args["-C"]:
        args["-A"] = args["-C"]
        args["-B"] = args["-C"]
    after = parse_jobs(args["-A"]) if args["-A"] else 0
    before = parse_jobs(args["-B"]) if args["-B"] else 0

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
        "strip": args["--strip"], "max_count": max_count,
        "ordered": args["--ordered"],
        "window": window,
        "after": after, "before": before,
    }


# File utilities

def is_within(path, parent):
    try:
        return os.path.commonpath([os.path.abspath(path), os.path.abspath(parent)]) == os.path.abspath(parent)
    except ValueError:
        return False


def display(path):
    p = Path(os.path.abspath(path))
    try:
        rel = p.relative_to(Path(os.path.abspath("."))).as_posix()
        return "./" + rel if rel != "." else "."
    except ValueError:
        return p.as_posix()


def is_probably_text(path):
    try:
        with open(path, "rb") as f:
            return b"\x00" not in f.read(8192)
    except Exception:
        return False



MD_RE = re.compile(
    r'^```[^`\n]*$'                            # fenced code block
    r'|^[=-]{3,}\s*$'                          # setext header underline
    r'|^#{1,6}\s+'                             # headers
    r'|^>\s?'                                  # blockquote
    r'|^[\t ]*[-*+]\s+'                        # unordered list
    r'|^[\t ]*\d+\.\s+'                        # ordered list
    r'|^[-*_]{3,}\s*$'                         # hr
    r'|\[([^\]]*)\]\([^)]*\)'                  # link [text](url)
    r'|!\[([^\]]*)\]\([^)]*\)'                 # image ![alt](url)
    r'|\*\*\*([^*\n]+)\*\*\*'                  # bold+italic
    r'|\*\*([^*\n]+)\*\*'                      # bold
    r'|__([^_\n]+)__'                          # bold alt
    r'|(?<!\*)\*([^*\n]+)\*(?!\*)'             # italic *
    r'|(?<!_)_([^_\n]+)_(?!_)'                 # italic _
    r'|~~([^~\n]+)~~'                          # strikethrough
    r'|`([^`\n]+)`',                           # code span
    flags=re.MULTILINE
)


def md_replace(m):
    for g in m.groups():
        if g is not None:
            return g
    return ''


def strip_markup(text):
    text = MD_RE.sub(md_replace, text)
    text = re.sub(r'^\|(.+)\|$', r'\1', text, flags=re.MULTILINE)
    parser = MarkupParser()
    parser.feed(text)
    return ''.join(parser._parts)


def is_markup_file(path):
    return Path(path).suffix.lower() in MARKUP_EXTS


def safe_transfer(src, dst, do_move):
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


def resolve_flat(outdir, rel):
    name = Path(rel).name
    target = outdir / name
    if not target.exists():
        return target
    stem, suffix = Path(name).stem, Path(name).suffix
    for n in range(1, 10000):
        target = outdir / f"{stem}.conflict-{n}{suffix}"
        if not target.exists():
            return target


def pick_tmp_root():
    if sys.platform != "win32":
        shm = Path("/dev/shm")
        if shm.exists() and os.access(str(shm), os.W_OK):
            return shm
    return Path(tempfile.gettempdir())


def detect(path):
    p = Path(os.path.abspath(os.path.expanduser(str(path))))
    if not p.exists():
        die(f"Input does not exist: {p}")
    if p.is_dir():
        return {"kind": "dir", "path": p}
    if p.is_file():
        return {"kind": "archive" if is_archive(p.name) else "file", "path": p}
    die(f"Unsupported input type: {p}")


def open_zst_stream(path):
    if shutil.which("zstd"):
        proc = subprocess.Popen(["zstd", "-d", "-T0", str(path), "-c"], stdout=subprocess.PIPE)
        return proc.stdout, lambda: (proc.terminate(), proc.wait())
    try:
        import zstandard as zstd_mod
    except ImportError:
        die("Decompressing .tar.zst requires 'zstd' command or 'zstandard' package")
    raw = open(str(path), 'rb')
    return zstd_mod.ZstdDecompressor().stream_reader(raw), raw.close


def extract_archive(archive, dest):
    path = str(archive)
    if path.endswith('.tar.zst'):
        stream, cleanup = open_zst_stream(path)
        try:
            with tarfile.open(fileobj=stream, mode="r|") as tf:
                kwargs = {"filter": "data"} if sys.version_info >= (3, 12) else {}
                tf.extractall(path=str(dest), **kwargs)
        finally:
            cleanup()
    elif path.endswith('.zip'):
        with zipfile.ZipFile(path, "r") as zf:
            zf.extractall(str(dest))
    else:
        with tarfile.open(path, 'r:*') as tf:
            kwargs = {"filter": "data"} if sys.version_info >= (3, 12) else {}
            tf.extractall(path=str(dest), **kwargs)


# File iteration

def should_include(rel, filters):
    if not filters:
        return True
    name = Path(rel).name
    for pat in filters.get("exclude", []):
        if fnmatch.fnmatch(name, pat) or fnmatch.fnmatch(rel, pat):
            return False
    inc = filters.get("include", [])
    return not inc or any(fnmatch.fnmatch(name, p) for p in inc)


def walk(root, filters, exclude=None, exts=None, rel_display=False):
    root = os.path.abspath(str(root))
    if os.path.isfile(root):
        name = os.path.basename(root)
        if exts and not any(name.endswith(e) for e in exts):
            return
        yield {"rel": name, "path": root, "display": display(root)}
        return
    ex = os.path.abspath(exclude) if exclude else None
    for cur, dirs, files in os.walk(root, followlinks=False):
        if ex:
            dirs[:] = sorted(d for d in dirs if not is_within(os.path.join(cur, d), ex))
        else:
            dirs[:] = sorted(dirs)
        for name in sorted(files):
            if exts and not any(name.endswith(e) for e in exts):
                continue
            p = os.path.join(cur, name)
            try:
                st = os.lstat(p)
            except OSError:
                continue
            if not stat.S_ISREG(st.st_mode):
                continue
            if ex and is_within(p, ex):
                continue
            rel = os.path.relpath(p, root).replace(os.sep, "/")
            if not should_include(rel, filters):
                continue
            yield {"rel": rel, "path": p, "display": rel if rel_display else display(p)}


def expand_archives(items, filters, temp_roots):
    archives = [it for it in items if is_archive(it["path"])]
    if not archives:
        return []
    result = []
    for arch in archives:
        tmp = Path(tempfile.mkdtemp(prefix="zxgrep.", dir=str(pick_tmp_root())))
        temp_roots.append(tmp)
        extract_archive(arch["path"], tmp)
        for ai in walk(tmp, filters, rel_display=True):
            result.append({
                "rel": arch["rel"] + "/" + ai["rel"],
                "path": ai["path"],
                "display": arch["display"] + "/" + ai["display"],
            })
    return result


# Pattern compilation

MODE_EXPR = {
    "substr": lambda w: re.escape(w),
    "exact":  lambda w: LEFT_BOUNDARY + re.escape(w) + RIGHT_BOUNDARY,
    "regex":  lambda w: w,
}


def compile_one(word, mode, case):
    flags = 0 if case else re.IGNORECASE
    try:
        pat = re.compile(MODE_EXPR[mode](word), flags)
    except re.error as ex:
        die(f"Regex compilation failed: {word!r}: {ex}")
    if mode == "regex":
        m = pat.search("")
        if m is not None and m.start() == m.end():
            die(f"Regex is not allowed to match empty string: {word!r}")
    return pat


def compile_all(words, mode, case):
    return [compile_one(w, mode, case) for w in words]


def compile_any(words, mode, case):
    flags = 0 if case else re.IGNORECASE
    if mode in ("substr", "exact"):
        uniq = sorted(dict.fromkeys(words), key=len, reverse=True)
        inner = "|".join(re.escape(w) for w in uniq)
        expr = (LEFT_BOUNDARY + "(?:" + inner + ")" + RIGHT_BOUNDARY) if mode == "exact" else inner
    else:
        expr = "|".join(f"(?:{w})" for w in words)
    try:
        return re.compile(expr, flags)
    except re.error as ex:
        die(f"Highlight regex compilation failed: {ex}")


def seq_match(pats, s, pos=0):
    for p in pats:
        m = p.search(s, pos)
        if not m: return False
        pos = m.end()
    return True


def window_match(raw, all_pats, window, ordered):
    line_hits = [{i for i, p in enumerate(all_pats) if p.search(l)} for l in raw]
    matched = set()
    end, need = len(raw), set(range(len(all_pats)))
    if ordered:
        for i in range(end):
            idx, lim = i, min(end, i + window)
            for pi in range(len(all_pats)):
                while idx < lim and pi not in line_hits[idx]: idx += 1
                if idx >= lim: break
                idx += 1
            else:
                matched.update(range(i, lim))
    else:
        for i in range(end):
            if set().union(*line_hits[i:i + window]) >= need:
                matched.update(range(i, min(end, i + window)))
    return matched


# Output helpers

def colorize(line, pat):
    return pat.sub(lambda m: f"{RED}{m.group(0)}{RESET}", line.rstrip("\r\n"))


def column(line, pat):
    m = pat.search(line)
    return (m.start() + 1) if m else 1



def output(item, matches, outdir, do_move, color, tty, is_list, is_name, any_pat, flat):
    if outdir:
        target = resolve_flat(outdir, item["rel"]) if flat else outdir / item["rel"]
        safe_transfer(item["path"], target, do_move)
        disp = display(target)
    else:
        disp = item["display"]

    if is_list or is_name:
        text = f"{CYAN}{disp}{RESET}" if color and tty else disp
        sys.stdout.write(text + "\n")
        sys.stdout.flush()
    else:
        ln_w = max(len(str(ln)) for ln, _, _ in matches)
        col_w = max((len(str(cn)) for _, cn, _ in matches if cn != 0), default=1)
        for ln, cn, line in matches:
            if cn == 0:
                prefix = f"{DIM}{disp}:{ln:0{ln_w}d}:{0:0{col_w}d}{RESET}" if color and tty else f"{disp}:{ln:0{ln_w}d}:{0:0{col_w}d}"
                sys.stdout.write(f"{prefix}: {line.rstrip('\r\n')}\n")
            else:
                prefix = f"{CYAN}{disp}:{ln:0{ln_w}d}:{cn:0{col_w}d}{RESET}" if color and tty else f"{disp}:{ln:0{ln_w}d}:{cn:0{col_w}d}"
                colored = colorize(line, any_pat) if tty else line.rstrip("\r\n")
                sys.stdout.write(f"{prefix}: {colored}\n")
            sys.stdout.flush()


# Special file extraction

def cmd_to_lines(prefix, build_cmd, timeout=None):
    fd, tmp = tempfile.mkstemp(suffix=".txt", prefix=prefix)
    try:
        os.close(fd)
        r = subprocess.run(build_cmd(tmp), capture_output=True, timeout=timeout)
        if r.returncode != 0:
            return None
        with open(tmp, "r", encoding="utf-8", errors="replace") as f:
            return f.readlines() or None
    except Exception:
        return None
    finally:
        try: os.unlink(tmp)
        except OSError: pass


def extract_pdf(path):
    if not shutil.which("pdftotext"):
        return None
    return cmd_to_lines("zxg_pdf_", lambda t: ["pdftotext", "-enc", "UTF-8", "-layout", str(path), t])


class MarkupParser(HTMLParser):
    BLOCK = frozenset({
        'p', 'div', 'br', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'li', 'tr',
        'blockquote', 'section', 'article', 'header', 'footer', 'nav', 'aside',
        'main', 'figure', 'figcaption', 'details', 'summary', 'dl', 'dt', 'dd',
        'ul', 'ol', 'table', 'thead', 'tbody', 'tfoot', 'th', 'td', 'hr', 'pre',
    })
    SKIP = frozenset({'script', 'style', 'head'})

    def __init__(self):
        super().__init__()
        self._parts = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        t = tag.lower()
        if t in self.SKIP: self._skip += 1
        if t in self.BLOCK: self._parts.append('\n')

    def handle_endtag(self, tag):
        t = tag.lower()
        if t in self.SKIP: self._skip = max(0, self._skip - 1)
        if t in self.BLOCK: self._parts.append('\n')

    def handle_data(self, data):
        if self._skip == 0:
            self._parts.append(data)


def extract_epub(path):
    try:
        with zipfile.ZipFile(path, "r") as zf:
            names = sorted(n for n in zf.namelist()
                           if n.lower().endswith(('.html', '.htm', '.xhtml'))
                           and not n.startswith('META-INF/') and n != 'mimetype')
            parser = MarkupParser()
            for name in names:
                try:
                    parser.feed(zf.read(name).decode('utf-8', errors='replace'))
                except Exception:
                    continue
            lines = [l + '\n' for l in ''.join(parser._parts).split('\n')]
            return lines if any(l.strip() for l in lines) else None
    except Exception:
        return None


def extract_ebook(path):
    if not shutil.which("ebook-convert"):
        return None
    return cmd_to_lines("zxg_eb_", lambda t: ["ebook-convert", str(path), t], timeout=120)


EXTRACTORS = {
    ".pdf": extract_pdf,
    ".epub": extract_epub,
    ".mobi": extract_ebook,
    ".azw3": extract_ebook,
}


def extract_lines(path):
    return EXTRACTORS.get(Path(path).suffix.lower(), lambda _: None)(path)


def strip_lines(raw):
    in_guard = False
    result = []
    for l in raw:
        if l.startswith('```'):
            in_guard = not in_guard
            result.append('')
        elif in_guard:
            result.append(l)
        else:
            result.append(strip_markup(l))
    return result


# Search worker

def process_file(args):
    item, all_pats, any_pat, opts = args
    path = item["path"]
    combine = any if opts["or"] else all

    if opts["name"]:
        nm = Path(item["rel"]).name
        ok = seq_match(all_pats, nm) if opts.get("ordered") else combine(p.search(nm) for p in all_pats)
        return (item, []) if ok else None

    is_special = Path(path).suffix.lower() in SPECIAL_EXTS
    if not is_special and not is_probably_text(path):
        return None

    do_strip = opts.get("strip") and is_markup_file(path)
    maxc = opts.get("max_count")
    window = opts.get("window", 0)

    try:
        if is_special:
            raw = extract_lines(path)
        else:
            with open(path, "r", encoding="utf-8", errors="replace", newline="") as f:
                raw = list(f)
            if do_strip:
                raw = strip_lines(raw)
        if raw is None:
            return None

        if opts["file"]:
            if opts.get("ordered"):
                it = iter(raw)
                if not all(any(p.search(l) for l in it) for p in all_pats):
                    return None
            else:
                found = {i for l in raw for i, p in enumerate(all_pats) if p.search(l)}
                if not (found if opts["or"] else found >= set(range(len(all_pats)))):
                    return None
            if opts["list"]:
                return (item, [])
            matches = [(ln, column(l, any_pat), l)
                       for ln, l in enumerate(raw, 1) if any_pat.search(l)]
        elif window:
            matched_lines = window_match(raw, all_pats, window, opts.get("ordered"))
            matches = [(ln + 1, m.start() + 1, raw[ln]) for ln in sorted(matched_lines) if (m := any_pat.search(raw[ln]))]
            if not matches:
                return None
            if opts["list"]:
                return (item, [])
        else:
            matched = [(ln, l) for ln, l in enumerate(raw, 1)
                       if (seq_match(all_pats, l) if opts.get("ordered") else combine(p.search(l) for p in all_pats))]
            if not matched:
                return None
            if opts["list"]:
                return (item, [])
            matches = [(ln, column(l, any_pat), l) for ln, l in matched]

        if maxc is not None:
            matches = matches[:maxc]
        after = opts.get("after", 0)
        before = opts.get("before", 0)
        if after or before:
            match_info = {ln: (cn, l) for ln, cn, l in matches}
            seen = set()
            expanded = []
            for ln in sorted(match_info):
                for i in range(max(1, ln - before), min(len(raw), ln + after) + 1):
                    if i not in seen:
                        seen.add(i)
                        expanded.append((i, 0, raw[i - 1]) if i not in match_info else (i,) + match_info[i])
            matches = expanded
        return (item, matches) if matches else None
    except Exception:
        return None


def process_batch(batch_args):
    items, all_pats, any_pat, opts = batch_args
    results = []
    for item in items:
        result = process_file((item, all_pats, any_pat, opts))
        if result is not None:
            results.append(result)
    return results


# Search engines

def run_stream(info, all_pats, any_pat, args, callback):
    tmp = Path(tempfile.mkdtemp(prefix="zxgrep_stream."))
    opts = {"file": args["file"], "list": args["list"], "name": args["name"], "or": args["or"], "ordered": args["ordered"], "window": args["window"], "strip": args["strip"], "max_count": args["max_count"], "after": args["after"], "before": args["before"]}
    stream, cleanup = open_zst_stream(str(info["path"]))
    try:
        with tarfile.open(fileobj=stream, mode="r|") as tf:
            for member in tf:
                if not member.isfile():
                    continue
                rel = member.name[2:] if member.name.startswith("./") else member.name
                if not should_include(rel, args["filters"]):
                    continue
                ef = tf.extractfile(member)
                if ef is None:
                    continue
                tp = tmp / "current"
                with open(str(tp), "wb") as f:
                    shutil.copyfileobj(ef, f)
                callback(process_file(({"rel": rel, "path": str(tp), "display": rel},
                                       all_pats, any_pat, opts)))
                try: tp.unlink()
                except FileNotFoundError: pass
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
        cleanup()


def run_python(items, all_pats, any_pat, args, callback):
    opts = {"file": args["file"], "list": args["list"], "name": args["name"], "or": args["or"], "ordered": args["ordered"], "window": args["window"], "strip": args["strip"], "max_count": args["max_count"], "after": args["after"], "before": args["before"]}
    jobs = args["jobs"]
    n = len(items)
    chunk_size = max(1, n // (jobs * 4))
    batches = []
    for i in range(0, n, chunk_size):
        batches.append((items[i:i + chunk_size], all_pats, any_pat, opts))
    with concurrent.futures.ProcessPoolExecutor(max_workers=jobs) as pool:
        futures = {pool.submit(process_batch, b): b for b in batches}
        for future in concurrent.futures.as_completed(futures):
            try:
                for result in future.result():
                    callback(result)
            except Exception:
                pass


def make_ugrep_item(fpath, walk_root, rel_display):
    p = Path(os.path.abspath(fpath))
    if not p.exists():
        return None
    try:
        rel = p.relative_to(Path(os.path.abspath(str(walk_root)))).as_posix()
    except ValueError:
        rel = p.name
    return {"rel": rel, "path": str(p), "display": rel if rel_display else display(p)}


UGREP_LINE_RE = re.compile(r"^(.*?):(\d+):(\d+):(.*)$", re.DOTALL)

def run_ugrep(walk_root, recursive, rel_display, args, callback):
    if not shutil.which("ugrep"):
        die("Missing required command: ugrep")

    root = str(walk_root)
    cmd = ["ugrep", "--no-messages", "--binary-files=without-match", "-H"]
    if recursive:
        cmd.append("-r")
    if not args["case"]:
        cmd.append("-i")
    if args["mode"] == "exact":
        cmd.append("-P")
    if args["mode"] == "substr":
        cmd.append("-F")
    for ext in SPECIAL_EXTS:
        cmd.extend(["-g", f"!*{ext}"])
    f = args["filters"]
    if f:
        norm = lambda p: p if p.startswith('*') else f"**/{p}"
        for p in f.get("include", []):
            cmd.extend(["-g", norm(p)])
        for p in f.get("exclude", []):
            cmd.extend(["-g", f"!{norm(p)}"])
    if args["list"]:
        cmd.append("-l")
    else:
        cmd.extend(["-n", "-k", "--color=never"])
    if args["max_count"] is not None:
        cmd.extend(["-m", str(args["max_count"])])
    wrap = (lambda w: f"(?<![0-9A-Za-z_]){re.escape(w)}(?![0-9A-Za-z_])") if args["mode"] == "exact" else (lambda w: w)
    if args["or"]:
        for w in args["words"]:
            cmd.extend(["-e", wrap(w)])
    else:
        for i, w in enumerate(args["words"]):
            cmd.extend(["-e", wrap(w)])
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
            item = make_ugrep_item(fp, walk_root, rel_display)
            if item:
                callback((item, []))
    else:
        results = {}
        for line in stdout.splitlines():
            m = UGREP_LINE_RE.match(line)
            if not m:
                continue
            fp, ln, cn, text = m.group(1), int(m.group(2)), int(m.group(3)), m.group(4)
            if fp not in results:
                item = make_ugrep_item(fp, walk_root, rel_display)
                if item:
                    results[fp] = (item, [])
                else:
                    continue
            results[fp][1].append((ln, cn, text + "\n"))
        for item, matches in results.values():
            callback((item, matches))
    return True


def run(args):
    info = detect(args["input"])
    all_pats = compile_all(args["words"], args["mode"], args["case"])
    any_pat = compile_any(args["words"], args["mode"], args["case"])
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
        output(result[0], result[1], outdir, args["move"], args["color"], tty,
               args["list"], args["name"], any_pat, args["flat"])

    if info["path"].name.endswith('.tar.zst') and args["stream"]:
        run_stream(info, all_pats, any_pat, args, callback)
        if outdir and found:
            eprint(f"Matched files have been {'moved' if args['move'] else 'copied'} to: {display(outdir)}")
        return found

    SUPPLEMENT_EXTS = ARCHIVE_EXTS + SPECIAL_EXTS
    use_ugrep = (args["ugrep"] and not args["stream"] and not args["name"]
                  and not (args["file"] and not args["or"]) and not args["strip"]
                  and not args["ordered"] and not args["window"]
                  and not args["after"] and not args["before"])

    temp_roots = []
    try:
        extracted = None
        if info["kind"] == "archive":
            temp_root = Path(tempfile.mkdtemp(prefix="zxgrep.", dir=str(pick_tmp_root())))
            temp_roots.append(temp_root)
            extract_archive(info["path"], temp_root)
            extracted = temp_root

        exclude = None
        if info["kind"] == "dir" and outdir:
            if is_within(str(outdir), str(info["path"])):
                exclude = outdir

        walk_root = extracted if info["kind"] == "archive" else info["path"]
        w_exclude = exclude if info["kind"] == "dir" else None
        w_reldisp = info["kind"] == "archive"
        recursive = info["kind"] in ("dir", "archive")

        ugrep_ok = use_ugrep and run_ugrep(walk_root, recursive, w_reldisp, args, callback)

        items = list(walk(walk_root, args["filters"], w_exclude,
                          SUPPLEMENT_EXTS if ugrep_ok else None, w_reldisp))
        arch_items = expand_archives(items, args["filters"], temp_roots)
        if arch_items:
            items = [it for it in items if not is_archive(it["path"])] + arch_items
        if ugrep_ok:
            arch_ids = {id(ai) for ai in arch_items}
            items = [it for it in items
                     if Path(it["path"]).suffix.lower() in SPECIAL_EXTS or id(it) in arch_ids]
        if items:
            run_python(items, all_pats, any_pat, args, callback)

        if outdir and found:
            eprint(f"Matched files have been {'moved' if args['move'] else 'copied'} to: {display(outdir)}")
        return found

    finally:
        for tr in temp_roots:
            shutil.rmtree(tr, ignore_errors=True)


# Installation

COMP_DIRS = [
    Path("/usr/share/bash-completion/completions"),
    Path("/usr/local/share/bash-completion/completions"),
    Path("/opt/homebrew/share/bash-completion/completions"),
    Path("/etc/bash_completion.d"),
]


ZSH_COMP_DIRS = [
    Path("/usr/share/zsh/site-functions"),
    Path("/usr/local/share/zsh/site-functions"),
    Path("/opt/homebrew/share/zsh/site-functions"),
]


def complete_dir():
    for d in COMP_DIRS:
        if d.is_dir():
            return d / PROGRAM
    return COMP_DIRS[1] / PROGRAM


def zsh_comp_dir():
    for d in ZSH_COMP_DIRS:
        if d.is_dir():
            return d / "_zxgrep"
    return None


def install_file(src, dst, mode, sudo):
    shutil.which("install") or die("Missing required command: install")
    if sudo:
        shutil.which("sudo") or die("Missing required command: sudo")
        subprocess.run(["sudo", "mkdir", "-p", str(dst.parent)], check=True)
        subprocess.run(["sudo", "install", "-m", f"{mode:o}", str(src), str(dst)], check=True)
    else:
        dst.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(["install", "-m", f"{mode:o}", str(src), str(dst)], check=True)


def install_unix():
    self_path = Path(__file__).resolve()
    bin_target = Path("/usr/local/bin") / PROGRAM
    comp_target = complete_dir()
    zsh_target = zsh_comp_dir()
    sudo = (os.geteuid() != 0)
    tmp = None

    targets = {comp_target, zsh_target} - {None}
    for d, name in [(d, PROGRAM) for d in COMP_DIRS] + [(d, "_zxgrep") for d in ZSH_COMP_DIRS]:
        old = d / name
        if old.exists() and old not in targets:
            subprocess.run((["sudo"] if sudo else []) + ["rm", "-f", str(old)], capture_output=True)

    try:
        fd, tmp_name = tempfile.mkstemp(prefix="zxgrep_completion_", text=True)
        os.close(fd)
        tmp = Path(tmp_name)

        install_file(self_path, bin_target, 0o755, sudo)
        print(f"Installed main program to: {bin_target}")

        tmp.write_text(BASH_COMPLETION_SCRIPT, encoding="utf-8")
        install_file(tmp, comp_target, 0o644, sudo)
        print(f"Installed Bash completion to: {comp_target}")

        zsh_hint = ""
        if zsh_target:
            tmp.write_text(ZSH_COMPLETION_SCRIPT, encoding="utf-8")
            install_file(tmp, zsh_target, 0o644, sudo)
            print(f"Installed Zsh completion to: {zsh_target}")
            zsh_hint = "\n  Zsh:  autoload -U compinit && compinit"

        print(f"\nIf completion still doesn't work, open a new shell or:\n  Bash: source {comp_target}{zsh_hint}")
    finally:
        if tmp and tmp.exists():
            try: tmp.unlink()
            except Exception: pass


def find_git_bash_dir():
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


def install_completion_windows():
    sys_dir = find_git_bash_dir()
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


def add_to_user_path_windows(directory):
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


def install_windows():
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

    install_completion_windows()

    path_dirs = [os.path.normcase(p) for p in os.environ.get("PATH", "").split(os.pathsep) if p]
    if os.path.normcase(str(scripts)) in path_dirs:
        print(f"\n{scripts} is already on your PATH.")
        print("You can now use:  zxgrep <args>")
    else:
        added = add_to_user_path_windows(str(scripts))
        if added:
            print(f"\nAdded {scripts} to your user PATH.")
            print("Please open a NEW terminal window, then use:  zxgrep <args>")
        else:
            print(f"\nCould not automatically add to PATH.")
            print(f"Please manually add this directory to your user PATH:\n  {scripts}")


def install_self():
    (install_windows if sys.platform == "win32" else install_unix)()


def clean():
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


def enable_ansi():
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
    enable_ansi()
    args = parse(argv)

    actions = {
        "help":             lambda: (usage(), 0)[1],
        "install":          lambda: (install_self(), 0)[1],
        "print-completion": lambda: (print(BASH_COMPLETION_SCRIPT, end=""), 0)[1],
        "clean":            lambda: (clean(), 0)[1],
    }
    if args["action"] in actions:
        return actions[args["action"]]()

    return 0 if run(args) else 1


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except KeyboardInterrupt:
        raise SystemExit(130)
    except subprocess.CalledProcessError as e:
        die(f"External command failed, exit code={e.returncode}")