# zxgrep

A grep-like command for zstd compressed files (also directories and regular files), but with my personal flavor.

## Usage

### Example

```bash
# test.tar.zst
# ├── test1.txt
# │   ├── line1: "zxgrep is awesome"
# │   └── line2: "but not this line"
# └── test2.txt
#     └── line3: "awesome zxgrep" (with two leading empty lines)
printf "zxgrep is awesome\nbut not this line\n" > /tmp/test1.txt
printf "\n\nawesome zxgrep\n" > /tmp/test2.txt
tar -I zstd -cf /tmp/test.tar.zst -C /tmp test1.txt test2.txt

zxgrep /tmp/test.tar.zst awesome xg
```

### Output

test1.txt:1:2: z<mark>**xg**</mark>rep is <mark>**awesome**</mark>  
test2.txt:3:1: <mark>**awesome**</mark> z<mark>**xg**</mark>rep

> See the [Manual](#manual) for more usage details.

## Platforms

Linux / Windows

macOS might be available, but hasn't been tested yet.

## Requirements

- python
- [zstd](https://github.com/facebook/zstd) (optional, or [zstandard](https://pypi.org/project/zstandard/))
- [poppler](https://poppler.freedesktop.org/) (optional)
- [calibre](https://calibre-ebook.com/) (optional)
- [ugrep](https://github.com/Genivia/ugrep) (optional)

## Installation

1. Run `curl -fsSL https://raw.githubusercontent.com/Caturra000/zxgrep/script/install | python`
2. OK. Let's try a simple [example](#example) using the `zxgrep` command.

Alternatively,

1. Clone this project or copy the `zxgrep.py` file.
2. Run `python zxgrep.py --install` and follow the instructions.
3. OK. Let's try a simple [example](#example) using the `zxgrep` command.

## Manual

```python
def usage():
    print(f"""Usage:
  zxgrep INPUT WORD1 [WORD2 ...]
  zxgrep INPUT WORD1 [WORD2 ...] --file
  zxgrep INPUT WORD1 [WORD2 ...] --file -o OUTDIR
  zxgrep INPUT WORD1 [WORD2 ...] -O
  zxgrep INPUT WORD1 [WORD2 ...] -x
  zxgrep INPUT WORD1 [WORD2 ...] -r
  zxgrep INPUT WORD1 [WORD2 ...] -s
  zxgrep INPUT WORD1 [WORD2 ...] --or
  zxgrep INPUT WORD1 [WORD2 ...] --include '*.py' --exclude 'test_*'
  zxgrep INPUT WORD1 [WORD2 ...] -l
  zxgrep INPUT WORD1 [WORD2 ...] -m 5
  zxgrep INPUT WORD1 [WORD2 ...] -C 2
  zxgrep INPUT WORD1 [WORD2 ...] -N
  zxgrep INPUT WORD1 [WORD2 ...] -j 8
  zxgrep INPUT WORD1 [WORD2 ...] --stream
  zxgrep INPUT WORD1 [WORD2 ...] -O --flat
  zxgrep INPUT WORD1 [WORD2 ...] --ugrep
  zxgrep INPUT WORD1 [WORD2 ...] --strip
  zxgrep --install
  zxgrep --print-bash-completion
  zxgrep --clean

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
       zxgrep ./docs exec --strip
       zxgrep archive.tar.zst exec --strip -x -s

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
       zxgrep ./docs report -N
       zxgrep ./docs 'report.*2024' -N -r

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
        zxgrep ./docs exec task        # AND: must contain both exec and task
        zxgrep ./docs exec task --or   # OR:  contains exec or task

~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~ Filtering ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

  14) --include GLOB:
      Only search files whose basename matches the specified glob pattern.
      Matching is based on the file basename (without directories).
      Can be specified multiple times to add multiple patterns (any match is accepted).
      Example:
        zxgrep ./docs exec --include '*.py'
        zxgrep ./docs exec --include '*.py' --include '*.js'

  15) --exclude GLOB:
      Exclude files whose names match the specified glob pattern.
      Matches against basename and relative path (either match excludes).
      Can be specified multiple times to add multiple patterns.
      Example:
        zxgrep ./docs exec --exclude '*.log'
        zxgrep ./docs exec --exclude 'node_modules' --exclude '*.min.js'

~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~ Output ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

  16) -l / --list-files:
      Only list matched file paths, do not output matched lines.
      - In default mode: list files that have at least one line matching all keywords
      - In --file mode: list files that contain all keywords

  17) -m / --max-count N:
      Stop after N matches per file.
      In line mode: output at most N matching lines per file.
      In --file mode: output at most N lines (among those containing any keyword).
      Has no effect with -l or -N.
      Example:
        zxgrep ./docs exec -m 3
        zxgrep ./docs exec task --file -m 5

  18) -A / -B / -C N:
      Show N lines of context around each match.
      -A N: show N lines after each match.
      -B N: show N lines before each match.
      -C N: show N lines before and after each match (equivalent to -A N -B N).
      Context lines are displayed with column 0 (dimmed path), match lines
      keep the usual highlighting. Overlapping windows from adjacent matches
      are merged automatically.
      Has no effect with -l or -N.
      Example:
        zxgrep ./docs exec -C 2
        zxgrep ./docs exec -A 3 -B 1

  19) Default output includes line and column numbers:
      Like:
        path/to/file.txt:12:8: matched line

  20) Path coloring:
      Paths are colored by default.
      To avoid affecting VSCode's path:line:col recognition, you may disable path coloring.
      Disable:
        --no-color-path
      Explicitly enable (default behavior):
        --color-path

  21) -o OUTDIR / --outdir OUTDIR  /  -O / --auto-outdir:
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

  22) --flat:
      Flatten output directory structure (only effective with -o or -O).
      Instead of preserving the original directory hierarchy, all matched files
      are placed directly in the target directory (single level).
      If multiple files have the same name, conflicts are resolved by appending
      .conflict-N before the file extension.
      Example:
        zxgrep ./docs exec task -O --flat
        # Results in: zxgrep_exec+task/file1.txt, zxgrep_exec+task/file1.conflict-1.txt

~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~ Performance ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

  23) -j / --jobs:
      Specify number of parallel worker processes.
      Default uses CPU core count.
      Search uses multi-process parallelism; output is streamed in real time (order not guaranteed).
      Example:
        zxgrep ./docs exec task -j 8
        zxgrep archive.tar.zst exec -j 4

  24) --stream:
      Stream processing for .tar.zst archives only.
      Instead of extracting the entire archive to a temporary directory,
      process files one by one directly from the tar stream.
      Avoids high temporary disk usage for large archives.
      For other archive formats, directories, or single files, this flag has no effect.
      Note: -j/--jobs is ignored in stream mode (processing is sequential).

  25) --ugrep:
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
        - -A / -B / -C modes (context lines)
        - PDF / EPUB / MOBI / AZW3 files (handled by Python, then merged)

~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~ Commands ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

   26) --install:
      Install to /usr/local/bin/zxgrep and bash completion (Unix).
      On Windows, creates zxgrep.cmd launcher and adds to user PATH.

   27) --clean:
      Clean up all auto-generated output directories in the current directory (prefixed with zxgrep_).
      You will be prompted for confirmation before deletion.

~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~ Exit Codes ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

   28) Exit codes:
       0  match found
       1  no match found
       2  error (invalid arguments, missing dependencies, etc.)

Examples:
  zxgrep archive.tar.zst exec task
  zxgrep archive.tar.gz exec task
  zxgrep archive.tgz exec task
  zxgrep archive.tar.bz2 exec task
  zxgrep archive.tar.xz exec task
  zxgrep archive.zip exec task
  zxgrep ./docs exec task
  zxgrep ./docs/a.txt exec task
  zxgrep ./docs/report.pdf exec task
  zxgrep ./docs/book.epub hello world
  zxgrep ./docs/book.mobi chapter
  zxgrep ./docs/book.azw3 introduction
  zxgrep archive.tar.zst exec task --file
  zxgrep ./docs exec task -O
  zxgrep ./docs exec task --exact
  zxgrep ./docs 'exec(ute)?' --regex
  zxgrep ./docs 'exec.*task' --regex -s
  zxgrep ./docs exec task --or
  zxgrep ./docs exec task --or -l
  zxgrep ./docs exec --include '*.py'
  zxgrep ./docs exec --include '*.py' --include '*.js' --exclude 'test_*'
  zxgrep ./docs exec task -l
  zxgrep ./docs exec task -m 5
  zxgrep ./docs exec task -C 2
  zxgrep ./docs exec task -O --move
  zxgrep ./docs exec task -O --flat
  zxgrep ./docs report -N
  zxgrep ./docs 'report.*2024' -N -r
  zxgrep ./docs exec task -j 4
  zxgrep archive.tar.zst exec task --stream
  zxgrep ./docs exec task --ugrep
  zxgrep ./docs exec task --ugrep --file --or
  zxgrep ./docs exec task --strip
  zxgrep --clean
""")
```

## Note

Please note that `zxgrep` is intended for personal use and is not designed for performance.

If you need better performance, try adding the experimental `--ugrep` option.

## License

MPL 2.0
