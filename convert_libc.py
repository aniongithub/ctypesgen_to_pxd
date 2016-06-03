#!/usr/bin/env python3

from os import open, O_RDWR, makedirs, devnull
from os.path import abspath, exists, isfile, isdir, dirname
from subprocess import check_call, check_output, STDOUT
from re import compile as compile_re, M
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


_include_pattern = compile_re(r'^ (/usr/[+\-./0-9@A-Z_a-z]+)$', M)

_null_fd = open(devnull, O_RDWR)


def main(dest_base='./converted_headers', *args,
         root=dirname(abspath(__file__))):
    cc1plus = check_output(
        ('gcc', '-print-prog-name=cc1plus'),
        stdin=_null_fd,
        stderr=_null_fd,
    ).decode('UTF-8', 'ignore').split('\n',1)[0]

    includepaths = _include_pattern.findall(check_output(
        (cc1plus, '-v'),
        stdin=_null_fd,
        stderr=STDOUT,
    ).decode('UTF-8', 'ignore').split('\n\n',1)[0])

    for header in _HEADERS:
        src_paths = [
            abspath('%s/%s.h' % (includepath, header))
            for includepath in includepaths
        ]
        for src_path in src_paths:
            if exists(src_path) and isfile(src_path):
                break
        else:
            print('\nHeader does not exists: ' + header + '. Searched in:',
                  *src_paths, file=stderr, sep='\n')
            continue

        dest_path = abspath('%s/%s.pxd' % (dest_base, header))
        if exists(dest_path):
            print('\nAlready converted:', dest_path, file=stderr)
            continue

        dest_dir = dirname(dest_path)
        if not exists(dest_dir):
            makedirs(dest_dir)
            check_call(('touch', dest_dir + '/__init__.pxd'))
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
