# zxgrep

A grep-like command for zstd compressed files (also directories and regular files), but with my personal flavor.

## Usage

### Simple example

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

Output (with highlight):

test1.txt:1:2: z**xg**rep is **awesome**  
test2.txt:3:1: **awesome** z**xg**rep

### More details

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
  zxgrep INPUT WORD1 [WORD2 ...] -N
  zxgrep INPUT WORD1 [WORD2 ...] -j 8
  zxgrep INPUT WORD1 [WORD2 ...] --stream
  zxgrep INPUT WORD1 [WORD2 ...] -O --flat
  zxgrep --install
  zxgrep --print-bash-completion
  zxgrep --clean

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
       zxgrep ./docs exec task        # AND: must contain both exec and task
       zxgrep ./docs exec task --or   # OR:  contains exec or task

  8) --include GLOB:
     Only search files whose basename matches the specified glob pattern.
     Matching is based on the file basename (without directories).
     Can be specified multiple times to add multiple patterns (any match is accepted).
     Example:
       zxgrep ./docs exec --include '*.py'
       zxgrep ./docs exec --include '*.py' --include '*.js'

  9) --exclude GLOB:
     Exclude files whose names match the specified glob pattern.
     Matches against basename and relative path (either match excludes).
     Can be specified multiple times to add multiple patterns.
     Example:
       zxgrep ./docs exec --exclude '*.log'
       zxgrep ./docs exec --exclude 'node_modules' --exclude '*.min.js'

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
        zxgrep ./docs report -N
        zxgrep ./docs 'report.*2024' -N -r

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
        zxgrep ./docs exec task -O --flat
        # Results in: zxgrep_exec+task/file1.txt, zxgrep_exec+task/file1.conflict-1.txt

  16) Text file detection:
      For directory/single-file input, obvious binary files are skipped (simple NUL-byte check).

  17) -j / --jobs:
      Specify number of parallel worker processes.
      Default uses CPU core count.
      Search uses multi-process parallelism; output is streamed in real time (order not guaranteed).
      Example:
        zxgrep ./docs exec task -j 8
        zxgrep archive.tar.zst exec -j 4

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
  zxgrep archive.tar.zst exec task
  zxgrep ./docs exec task
  zxgrep ./docs/a.txt exec task
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
  zxgrep ./docs exec task -O --move
  zxgrep ./docs exec task -O --flat
  zxgrep ./docs report -N
  zxgrep ./docs 'report.*2024' -N -r
  zxgrep ./docs exec task -j 4
  zxgrep archive.tar.zst exec task --stream
  zxgrep --clean
""")
```

## Platform

Linux

## Installation

1. Clone this project or just copy the single file (`zxgrep.py`).
2. Run `python3 zxgrep.py --install` and follow the instructions.
3. OK. Let's try a [simple example](#simple-example) using the `zxgrep` command.

## License

MPL 2.0
