"""Non-blocking process exclusion on Windows and Unix."""

import errno
import os


def acquire_process_lock(path):
    """Return an open locked file; closing it releases the process lock."""
    handle = path.open("a+b")
    try:
        if os.name == "nt":
            import msvcrt

            # Windows locks a byte range. Ensure the file contains that byte.
            if handle.seek(0, os.SEEK_END) == 0:
                handle.write(b"\0")
                handle.flush()
            handle.seek(0)
            try:
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError as exc:
                if exc.errno in (errno.EACCES, errno.EAGAIN, errno.EDEADLK):
                    raise BlockingIOError("Another equities process holds the lock") from exc
                raise
        else:
            import fcntl

            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BaseException:
        handle.close()
        raise
    return handle
