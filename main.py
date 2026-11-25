"""Entry for make.fun's One-Line Office Joke Laugh-Off.

The program drops exactly one joke line into ``$MAKEFUN_OUTPUT_DIR/entry.txt``
(``/output`` when the variable is unset). The line never changes between
runs, so two runs always produce byte-identical output.

The sealed scorer only requires UTF-8 text of 1-200 characters that is
not whitespace-only. This entrypoint is stricter on purpose: it refuses
every control character (tab and newline included), every C1 control,
a leading BOM, Unicode line/paragraph separators, invisible format
characters, and lone surrogates, and it refuses leading or trailing
whitespace, so the published file is one clean line and nothing else.

Publishing is atomic: the line is staged in a temp file created inside
the output directory itself (never ``/tmp``, which can live on another
filesystem than a bind-mounted output dir) and renamed into place with
``os.replace``. Both the temp file and the published target are read
back and checked byte-for-byte, the file is fsynced, the directory is
fsynced after the rename, and temp files abandoned by a crashed
earlier run are swept before writing.
"""

import os
import stat
import tempfile
import time
import unicodedata

JOKE = (
    "I CC'd myself on the email so at least one person in this "
    "company would actually read it."
)

DEFAULT_OUTPUT_DIR = "/output"
TEMP_PREFIX = ".entry-"
TEMP_SUFFIX = ".tmp"
# A temp file this old can only belong to a dead run: a live run holds
# its temp file for milliseconds. The age guard also keeps a
# concurrent run from ever sweeping another run's fresh temp file.
STALE_TEMP_AGE_SECONDS = 60.0

# Unicode general categories rejected outright: Cc (controls, which
# covers C1 as well as tab and newline), Cf (format, including the
# BOM U+FEFF and zero-width marks), Cs (surrogates, which cannot be
# encoded as UTF-8), Zl and Zp (U+2028/U+2029 line and paragraph
# separators).
FORBIDDEN_CATEGORIES = ("Cc", "Cf", "Cs", "Zl", "Zp")


def _output_dir_from_env():
    """Resolve the output dir; blank or whitespace-only means default."""
    raw = os.environ.get("MAKEFUN_OUTPUT_DIR")
    if raw is None:
        return DEFAULT_OUTPUT_DIR
    cleaned = raw.strip()
    return cleaned or DEFAULT_OUTPUT_DIR


def _has_forbidden_char(text):
    """True when any character falls in a forbidden category/surrogate."""
    for ch in text:
        if unicodedata.category(ch) in FORBIDDEN_CATEGORIES:
            return True
        if 0xD800 <= ord(ch) <= 0xDFFF:
            return True
    return False


def _passes_gate(text):
    """Strict gate check for the joke line.

    Requires 1..200 characters, not whitespace-only, exactly one line:
    no control characters of any kind, no C1 controls, no BOM, no
    Unicode line/paragraph separators, no invisible format characters,
    no lone surrogates, and no leading or trailing whitespace.
    """
    if not isinstance(text, str):
        return False
    if not 1 <= len(text) <= 200:
        return False
    if not text.strip():
        return False
    if text != text.strip():
        return False
    if _has_forbidden_char(text):
        return False
    return True


def _remove_stale_temp_files(out_dir):
    """Delete temp files a crashed earlier run left behind.

    Only files with our own prefix/suffix, only older than
    STALE_TEMP_AGE_SECONDS. Best effort: a failure here must never
    block the run.
    """
    now = time.time()
    try:
        names = os.listdir(out_dir)
    except OSError:
        return 0
    removed = 0
    for name in names:
        if not (name.startswith(TEMP_PREFIX) and name.endswith(TEMP_SUFFIX)):
            continue
        path = os.path.join(out_dir, name)
        try:
            if now - os.path.getmtime(path) < STALE_TEMP_AGE_SECONDS:
                continue
            os.unlink(path)
            removed += 1
        except OSError:
            continue
    return removed


def _verify_written_bytes(path, expected):
    """Read a file back and demand exact bytes plus a gate-passing line."""
    with open(path, "rb") as f:
        back = f.read()
    if back != expected:
        raise SystemExit("read-back mismatch in %s" % path)
    try:
        text = back.decode("utf-8")
    except UnicodeDecodeError:
        raise SystemExit("read-back is not valid UTF-8 in %s" % path)
    if not _passes_gate(text):
        raise SystemExit("read-back fails the gate check in %s" % path)
    return text


def _fsync_directory(out_dir):
    """Flush the directory entry so the rename survives a crash."""
    dir_fd = os.open(out_dir, os.O_RDONLY)
    try:
        os.fsync(dir_fd)
    finally:
        os.close(dir_fd)


def _stage_payload(fd, tmp_path, payload):
    """Write payload into the staged temp file, fsync, and re-check it.

    The descriptor from mkstemp is closed here; the caller keeps the
    path so it can publish the file or clean it up afterwards.
    """
    os.chmod(tmp_path, 0o644)
    with os.fdopen(fd, "wb") as f:
        written = f.write(payload)
        if written != len(payload):
            raise SystemExit(
                "short write: %d of %d bytes" % (written, len(payload))
            )
        f.flush()
        os.fsync(f.fileno())
    os.chmod(tmp_path, 0o644)

    # Verify the staged file before it becomes the published one.
    _verify_written_bytes(tmp_path, payload)


def _publish_entry(out_dir, payload):
    """Stage payload atomically in out_dir and publish it as entry.txt."""
    _remove_stale_temp_files(out_dir)

    fd, tmp_path = tempfile.mkstemp(
        prefix=TEMP_PREFIX, suffix=TEMP_SUFFIX, dir=out_dir
    )
    try:
        _stage_payload(fd, tmp_path, payload)

        target = os.path.join(out_dir, "entry.txt")
        os.replace(tmp_path, target)
        os.chmod(target, 0o644)
        _fsync_directory(out_dir)

        # Verify the published target itself, and its mode.
        _verify_written_bytes(target, payload)
        mode = stat.S_IMODE(os.stat(target).st_mode)
        if mode != 0o644:
            raise SystemExit("output mode is not 0644: %o" % mode)
    finally:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)


def main():
    out_dir = _output_dir_from_env()
    os.makedirs(out_dir, exist_ok=True)

    if not _passes_gate(JOKE):
        raise SystemExit("entry line fails the gate check")

    _publish_entry(out_dir, JOKE.encode("utf-8"))


if __name__ == "__main__":
    main()
