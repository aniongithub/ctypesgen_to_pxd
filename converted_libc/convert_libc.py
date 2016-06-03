#!/usr/bin/env python3

from os.path import exists, isfile
from subprocess import check_call
from sys import argv, stderr


_HEADERS = sorted('''
    assert complex ctype errno fenv float inttypes iso646 limits
    locale math setjmp signal stdalign stdarg stdatomic stdbool
    stddef stdint stdio stdlib stdnoreturn string tgmath threads
    time uchar wchar wctype
'''.split())


def main(args=()):
    for header in _HEADERS:
        src_path = '/usr/include/%s.h' % header
        dest_path = './%s.pxd' % header
        if exists(src_path) and isfile(src_path) and not exists(dest_path):
            print('\nConverting:', dest_path, file=stderr)
            check_call(
                ('../ctypesgen_to_pxd.py',
                 *args, '-t=h', '-f="<%s.h>"' % header,
                 src_path, dest_path),
            )


if __name__ == '__main__':
    main(argv[1:])
