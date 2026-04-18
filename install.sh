#!/usr/bin/env python3
import urllib.request, sys, os, tempfile, subprocess

URL = "https://raw.githubusercontent.com/Caturra000/zxgrep/master/zxgrep.py"

def main():
    print(f"Downloading zxgrep from {URL} ...")
    with urllib.request.urlopen(URL) as r:
        code = r.read()

    tmp = tempfile.NamedTemporaryFile(suffix=".py", delete=False)
    try:
        tmp.write(code)
        tmp.close()
        subprocess.check_call([sys.executable, tmp.name, "--install"])
    finally:
        os.unlink(tmp.name)

if __name__ == "__main__":
    main()