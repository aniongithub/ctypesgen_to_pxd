#!/usr/bin/env python3

from os import makedirs
from os.path import abspath, exists, isfile, isdir, dirname
from subprocess import check_call
from sys import argv, stderr


# Standard headers as of POSIX.1-2008 (Base Specifications, Issue 7)
# http://pubs.opengroup.org/onlinepubs/9699919799/

_HEADERS = '''
    aio
    arpa/inet
    assert
    complex
    cpio
    ctype
    dirent
    dlfcn
    errno
    fcntl
    fenv
    float
    fmtmsg
    fnmatch
    ftw
    glob
    grp
    iconv
    inttypes
    iso646
    langinfo
    libgen
    limits
    locale
    math
    monetary
    mqueue
    ndbm
    net/if
    netdb
    netinet/in
    netinet/tcp
    nl_types
    poll
    pthread
    pwd
    regex
    sched
    search
    semaphore
    setjmp
    signal
    spawn
    stdarg
    stdbool
    stddef
    stdint
    stdio
    stdlib
    string
    strings
    stropts
    sys/ipc
    sys/mman
    sys/msg
    sys/resource
    sys/select
    sys/sem
    sys/shm
    sys/socket
    sys/stat
    sys/statvfs
    sys/time
    sys/times
    sys/types
    sys/uio
    sys/un
    sys/utsname
    sys/wait
    syslog
    tar
    termios
    tgmath
    time
    trace
    ulimit
    unistd
    utime
    utmpx
    wchar
    wctype
    wordexp
'''.split()


def main(dest_dir='./converted_headers', *args,
         root=dirname(abspath(__file__))):
    for header in _HEADERS:
        src_path = '/usr/include/%s.h' % header
        if not exists(src_path) or not isfile(src_path):
            continue

        dest_path = '%s/%s.pxd' % (dest_dir, header)
        if exists(dest_path):
            continue

        dest_dir = dirname(dest_path)
        if not exists(dest_dir):
            makedirs(dest_dir)
        elif not isdir(dest_dir):
            continue

        print('\nConverting:', src_path, file=stderr)
        check_call(
            (root + '/ctypesgen_to_pxd.py',
             *args, '-t=h', '-f="<%s.h>"' % header,
             src_path, dest_path),
        )


if __name__ == '__main__':
    main(*argv[1:])
