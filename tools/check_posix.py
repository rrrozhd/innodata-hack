"""Run the organizers' checker with POSIX path handling on Windows.

check_submission.py compares paths built with pathlib (backslashes on Windows)
against names stored with forward slashes, so on Windows it reports reported
folders and code folders as missing even when they are present. The judges run
it on a POSIX system. This wrapper loads the unmodified checker and swaps its
`Path` for a POSIX-flavoured one, so a Windows run gives the result a judge sees.

    python tools/check_posix.py TeamName_Track2.zip [checker options]
"""
import importlib.util
import pathlib
import sys


class PosixPath(pathlib.PurePosixPath):
    """Pure POSIX path arithmetic, with the few filesystem calls the checker makes."""

    def _real(self):
        return pathlib.Path(str(self))

    def is_file(self):
        return self._real().is_file()

    def exists(self):
        return self._real().exists()

    def stat(self):
        return self._real().stat()

    def read_bytes(self):
        return self._real().read_bytes()

    def write_text(self, *args, **kwargs):
        return self._real().write_text(*args, **kwargs)


def main(argv) -> int:
    here = pathlib.Path(__file__).resolve().parent
    spec = importlib.util.spec_from_file_location("check_submission", here / "check_submission.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.Path = PosixPath
    argv = [a.replace("\\", "/") if not a.startswith("-") else a for a in argv]
    return mod.main(argv)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
