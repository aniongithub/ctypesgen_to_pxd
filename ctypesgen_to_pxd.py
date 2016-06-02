#!/usr/bin/env python3
# encoding: UTF-8

# Copyright 2016 Freie Universität Berlin
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.


from argparse import ArgumentParser, FileType
from json import loads
from logging import getLogger, basicConfig, WARN
from os.path import basename, splitext
from regex import compile, VERBOSE, VERSION1
from subprocess import Popen, PIPE, TimeoutExpired
from sys import stdin, stdout, stderr, argv
from textwrap import wrap


__author__ = 'René Kijewski  <rene.SURNAME@fu-berlin.de>'
__copyright__ = 'Copyright 2016 Freie Universität Berlin'
__credits__ = ['René Kijewski']
__license__ = 'Apache License, Version 2.0'
__version__ = '0.0.1'
__maintainer__ = 'René Kijewski'
__email__ = 'rene.SURNAME@fu-berlin.de'
__status__ = 'Prototype'


__all__ = ('convert',)

_logger = getLogger('ctypesgen_to_pxd')

NoneType = type(None)


def _anon_struct_name(variety, tag):
    return tag


def _stdint_gen():
    for prefix in ('', 'u'):
        for infix in ('', '_least', '_fast'):
            for width in ('8', '16', '32', '64'):
                yield ''.join((prefix, 'int', infix, width, '_t'))

        for infix in ('ptr', 'max'):
                yield ''.join((prefix, 'int', infix, '_t'))


_STDDEF_TYPES = sorted('ptrdiff_t size_t wchar_t'.split())
_STDINT_TYPES = sorted(_stdint_gen())

_SIMPLE_TYPES = frozenset('''
    int char short void size_t float double
'''.split()) | set(_STDDEF_TYPES + _STDINT_TYPES)


def _format_CtypesSimple(ctype):
    name = ctype.get('name')
    if name in _SIMPLE_TYPES:
        signed = ctype.get('signed')
        longs = ctype.get('longs') or 0
        return (
            'unsigned ' if not signed else 'signed ' if name == 'int' else '',
            'long ' * longs,
            name
        )
    else:
        _logger.warn('Unknown CtypesSimple name=%r', name)
        return False


def _convert_constant(definition, f_out, indent_level):
    name = definition.get('name')
    value = definition.get('value')

    if not name or not value:
        _logger.warn('Unknown constant name=%r value=%r', name, value)
        return False

    _put(f_out, indent_level, 'cdef enum:  # was a constant')
    _put(f_out, indent_level + 1, name, ' = (', value, ')')
    return True


def _format_rhs(definition):
    klass = definition.get('Klass')

    if klass == 'ConstantExpressionNode':
        value = definition.get('value')
        if value is not None:
            return value,

    elif klass == 'BinaryExpressionNode':
        left = definition.get('left')
        right = definition.get('right')

        if not all(isinstance(i, dict) for i in (left, right)):
            _logger.error('Unknown BinaryExpressionNode type(left)=%r '
                          'type(right)=%r', type(left), type(right))
            return False

        name = definition.get('name')
        if name == 'addition':
            left_args = _format_rhs(left)
            if not left_args:
                return left_args

            right_args = _format_rhs(right)
            if not right_args:
                return right_args

            return ('((', *left_args, ') + (', *right_args, '))')

    elif klass == 'IdentifierExpressionNode':
        name = definition.get('name')
        if not name:
            _logger.error('Unknown IdentifierExpressionNode name=%r', name)

        return name,

    _logger.warn('Unsupported rhs Klass=%r', klass)
    return False


def _convert_enum(definition, f_out, indent_level):
    name = definition.get('name')
    fields = definition.get('fields')

    if not name or not isinstance(fields, (list, NoneType)):
        _logger.error('Unknown enum name=%r type(fields)=%r',
                      name, type(fields))
        return False

    printed = None
    if fields:
        for field in fields:
            field_name = field.get('name')
            if not field_name:
                _logger.error('Unknown enum field name=%r', field_name)
                continue

            ctype = field.get('ctype')
            if not isinstance(ctype, dict):
                _logger.error('Unknown enum field ctype=%r', ctype)
                continue

            value_args = _format_rhs(ctype)
            if not value_args:
                continue

            if not printed:
                _put(f_out, indent_level, 'cdef enum ', name or '', ':')
                printed = True

            _put(f_out, indent_level + 1, field_name, ' = (', *value_args, ')')

    return printed


def _format_function(definition):
    _absent = object()

    variadic = definition.get('variadic')

    returns = definition.get('return', _absent)
    if returns is _absent:
        returns = definition.get('restype')

    args = definition.get('args', _absent)
    if args is _absent:
        args = definition.get('argtypes')

    if not isinstance(returns, (dict, NoneType)):
        _logger.error('Unknown fuction type(return)=%r', type(returns))
        return False
    elif not isinstance(args, (list, NoneType)):
        _logger.error('Unknown fuction type(args)=%r', type(returns))
        return False

    if returns:
        returns_args = _convert_base_Klass(returns)
        if not returns_args:
            return returns_args
    else:
        returns_args = 'void',

    args_args = ()
    if args:
        for i in args:
            i_args = _convert_base_Klass(i)
            if not i_args:
                return i_args
            elif args_args:
                args_args = (*args_args, ', ', *i_args)
            else:
                args_args = i_args

    if variadic:
        if args_args:
            args_args = (*args_args, ', ...')
        else:
            args_args = ('...',)

    return returns_args, args_args


def _convert_function(definition, f_out, indent_level):
    name = definition.get('name')
    if not name:
        _logger.error('Unknown fuction name=%r', name)
        return False

    args = _format_function(definition)
    if not args:
        return args

    returns_args, args_args = args

    _put(f_out, indent_level,
         'cdef ', *returns_args, ' ', name, '(', *args_args, ')')
    return True


_match_verbatim = compile(r'''
    \A
    (
        \s*+
        (?>
            # unary plus / minus
            [+-]
            \s*+
        )*+

        (?>
            (?>
                #match integer or float
                (?: 0x)?+
                \d++
                (?>
                    [lL]
                |
                    (?> [.] \d++)?+ (?> [eE] [+-]?+ \d++)?+
                )?+
            )
        |
            (?>
                # match float without leading zero
                [.] \d++ (?> [eE] [+-]?+ \d++)?+
            )
        |
            (?>
                # match identifier
                [_a-zA-Z] [_a-zA-Z0-9]*+
            )
        |
            (?>
                # match parens
                \( (?1) \)
            )
        )
        \s*+

        (?>
            # match calculation
            [+\-*\/%] (?1)
        )?+
    )
    \Z
''', VERBOSE | VERSION1).match


_match_int = compile(r'''
    \A(?P<whole>
        \s*+
        (?>
            \( (?&whole) \)
            |
            (?>
                (?>
                    (?> (?P<minus> -) | [+] )
                    \s*+
                )?
                (?P<inner>
                    \( \s*+ (?&inner) \s*+ \)
                    |
                    (?>
                        (?P<prefix> 0x?)?
                        (?P<digits> \d+)
                        (?> [uU]? [lL]{0,2})
                    )
                )
            )
        )
        \s*+
    )\Z
''', VERBOSE | VERSION1).match

_match_identifier = compile(r'''
    \A(?P<whole>
        \s*+
        (?>
            \( (?&whole) \)
            |
            (?P<value> [_a-zA-Z] [_a-zA-Z0-9]*+ )
        )
        \s*+
    )\Z
''', VERBOSE | VERSION1).match


def _convert_macro(definition, f_out, indent_level):
    name = definition.get('name')
    value = definition.get('value')

    if name == value:
        _logger.info('Macro omitted: %s', name)
        return False  # sic

    elif isinstance(value, str):
        result = None
        for _ in (1,):
            m = _match_int(value)
            if m:
                capturesdict = m.capturesdict()
                minus = capturesdict.get('minus')
                prefix = capturesdict.get('prefix')
                digits, = capturesdict.get('digits')

                if minus:
                    minus, = minus
                if prefix:
                    prefix, = prefix

                result = int(digits, (10 if not prefix else
                                      16 if prefix == '0x' else 8))

                if minus:
                    result = -result
                break

            m = _match_identifier(value)
            if m:
                result, = m.capturesdict().get('value')
                break

            m = _match_verbatim(value)
            if m:
                _logger.info('Copying macro definition for %r verbatim', name)
                result = value
                break

        if result is not None:
            _put(f_out, indent_level, 'cdef enum:  # was a macro: %r' % value)
            _put(f_out, indent_level + 1, name, ' = (', result, ')')
            return True

    _logger.warn('Macro omitted: %s', name)
    _put(f_out, indent_level, ('# Macro omitted, not understood: %s = %r' %
                               (name, value)))
    return True


def _convert_struct(definition, f_out, indent_level, struct='struct'):
    name = definition.get('name')
    fields = definition.get('fields')
    if not name or not isinstance(fields, (list, NoneType)):
        _logger.error('Unknown %s data name=%r type(ctype)=%r',
                      struct, name, type(fields))
        return False

    name_args = _anon_struct_name(struct, name)
    if not name_args:
        return name_args

    if fields is None:
        _put(f_out, indent_level,
             'cdef ', struct, ' ', *name_args, '  # forward declaration')
    else:
        _put(f_out, indent_level, 'cdef ', struct, ' ', *name_args, ':')
        for field in fields:
            _convert_typedef(field, f_out, indent_level + 1,
                             include_cdef=False)

    return True


def _convert_union(definition, f_out, indent_level):
    return _convert_struct(definition, f_out, indent_level, struct='union')


def _convert_typedef_CtypesSimple(name, ctype, include_cdef):
    args = _format_CtypesSimple(ctype)
    if not args:
        return args

    return (
        'ctypedef ' if include_cdef else '',
        *args, ' ', name,
    )


def _convert_typedef_CtypesStruct(name, ctype, include_cdef):
    variety = ctype.get('variety')
    if variety not in ('struct', 'union'):
        _logger.error('Unknown CtypesStruct variety=%r', variety)
        return False

    tag = ctype.get('tag')
    if not tag:
        _logger.error('Unknown CtypesStruct tag=%r', tag)
        return False

    name_args = _anon_struct_name(variety, tag)
    if not name_args:
        return name_args

    if name_args == _anon_struct_name(variety, name):
        return False  # sic

    return ('ctypedef ' if include_cdef else '', *name_args, ' ', name)


def _convert_base_CtypesSimple(base):
    return _format_CtypesSimple(base)


def _convert_base_CtypesStruct(base):
    tag = base.get('tag')
    variety = base.get('variety')
    if not tag or not variety:
        _logger.error('Unsupported base Klass=%r tag=%r variety=%r',
                      klass, tag, variety)
        return False

    return _anon_struct_name(variety, tag)


def _convert_base_CtypesPointer(base):
    destination = base.get('destination')
    if not isinstance(destination, dict):
        _logger.error('Unsupported base Klass type(destination)=%r',
                     destination)
        return False

    args = _convert_base_Klass(destination)
    if not args:
        return args

    return (*args, '*')


def _convert_base_CtypesTypedef(base):
    name = base.get('name')
    if not name:
        _logger.error('Unsupported base Klass CtypesTypedef name=%r', name)
        return False

    return (name or identifier,)


def _convert_base_CtypesFunction(base):
    args = _format_function(base)
    if not args:
        return args

    returns_args, args_args = args

    return (*returns_args, '(', *args_args, ')')


def _convert_base_CtypesArray(base):
    args = _format_CtypesArray(base)
    if not args:
        return args

    base_args, count_args = args
    return (*base_args, '[', *count_args, ']')


def _convert_base_CtypesSpecial(base):
    name = base.get('name')
    if not name:
        _logger.error('Unsupported base Klass CtypesSpecial name=%r', name)

    if name == 'String':
        return 'char*',

    _logger.warn('Unknown CtypesSpecial name=%r', name)
    return False


_CONVERT_BASE_FUNS = {
    'CtypesArray': _convert_base_CtypesArray,
    'CtypesFunction': _convert_base_CtypesFunction,
    'CtypesPointer': _convert_base_CtypesPointer,
    'CtypesSimple': _convert_base_CtypesSimple,
    'CtypesSpecial': _convert_base_CtypesSpecial,
    'CtypesStruct': _convert_base_CtypesStruct,
    'CtypesTypedef': _convert_base_CtypesTypedef,
}


def _convert_base_Klass(base):
    klass = base.get('Klass')
    convert_fun = _CONVERT_BASE_FUNS.get(klass)
    if convert_fun:
        return convert_fun(base)
    else:
        _logger.warn('Unsupported base Klass=%r', klass)
        return False


def _format_CtypesArray(ctype):
    base = ctype.get('base')
    if not isinstance(base, dict):
        _logger.error('CtypesArray type(base)=%r', type(base))
        return False

    count = ctype.get('count')
    if not isinstance(count, (dict, NoneType)):
        _logger.error('CtypesArray type(count)=%r', type(count))
        return False

    if count is not None:
        count_args = _format_rhs(count)
        if not count_args:
            return count_args
    else:
        count_args = ()

    base_args = _convert_base_Klass(base)
    if not base_args:
        return base_args

    return base_args, count_args


def _convert_typedef_CtypesArray(name, ctype, include_cdef):
    args = _format_CtypesArray(ctype)
    if not args:
        return args

    base_args, count_args = _format_CtypesArray(ctype)
    return (
        'ctypedef ' if include_cdef else '',
        *base_args, ' ', name or '', '[', *count_args, ']',
    )


def _convert_typedef_CtypesPointer(name, ctype, include_cdef):
    destination = ctype.get('destination')
    if not isinstance(destination, dict):
        _logger.error('Unsupported base Klass type(destination)=%r',
                      destination)
        return False

    args = _convert_base_Klass(destination)
    if not args:
        return args

    return (
        'cdef ' if include_cdef else '',
        *args, '* ', name,
    )


def _convert_typedef_CtypesFunction(name, ctype, include_cdef):
    args = _format_function(ctype)
    if not args:
        return args

    returns_args, args_args = args

    return (
        'ctypedef ' if include_cdef else '',
        *returns_args, ' ', name, '(', *args_args, ')',
    )


def _convert_typedef_CtypesTypedef(name, ctype, include_cdef):
    base_name = ctype.get('name')
    if not base_name:
        _logger.error('Unknown CtypesTypedef name=%r', base_name)
        return False

    return (
        'cdef ' if include_cdef else '',
        base_name, ' ', name,
    )


_CONVERT_TYPEDEF_FUNS = {
    'CtypesArray': _convert_typedef_CtypesArray,
    'CtypesFunction': _convert_typedef_CtypesFunction,
    'CtypesPointer': _convert_typedef_CtypesPointer,
    'CtypesSimple': _convert_typedef_CtypesSimple,
    'CtypesStruct': _convert_typedef_CtypesStruct,
    'CtypesTypedef': _convert_typedef_CtypesTypedef,
}


def _convert_typedef(definition, f_out, indent_level, include_cdef=True):
    name = definition.get('name')
    ctype = definition.get('ctype')
    if not name or not isinstance(ctype, dict):
        _logger.error('Unknown typedef data name=%r type(ctype)=%r',
                      name, type(ctype))
        return False

    klass = ctype.get('Klass')

    convert_fun = _CONVERT_TYPEDEF_FUNS.get(klass)
    if convert_fun:
        args = convert_fun(name, ctype, include_cdef)
        if not args:
            return args

        _put(f_out, indent_level, *args)
        return True

    else:
        _logger.warn('Unknown typedef Klass=%r', klass)


def _convert_macro_function(definition, f_out, indent_level, include_cdef=True):
    name = definition.get('name')
    args = definition.get('args') or ()
    body = definition.get('body') or ''
    if not name:
        _logger.error('Unknown macro function name=%r', name)
        return False

    _logger.warn('Cannot convert macro function %s', name)
    _put(f_out, indent_level, ('# Unconvertable macro function: %s(%s) %r' %
                               (name, ', '.join(args), body)))
    return True


_CONVERT_FUNS = {
    'constant': _convert_constant,
    'enum': _convert_enum,
    'function': _convert_function,
    'macro': _convert_macro,
    'macro_function': _convert_macro_function,
    'struct': _convert_struct,
    'typedef': _convert_typedef,
    'union': _convert_union,
}


def _put(f_out, indent_level, *args, **kw):
    print('    ' * indent_level, *args, file=f_out, sep='')


def convert(definitions, f_out, import_from='*', indent_level=0):
    unknown_types = set()

    for h_name, items in (('stddef', _STDDEF_TYPES),
                          ('stdint', _STDINT_TYPES)):
        _put(f_out, indent_level, 'from libc.', h_name, ' cimport (')
        for line in wrap(', '.join(items), 79 - 4 * (indent_level + 1)):
            _put(f_out, indent_level + 1, line)
        _put(f_out, indent_level, ')')
        _put(f_out, indent_level)

    _put(f_out, indent_level,
         'cdef extern from ', import_from or '*', ' nogil:')

    for definition in definitions:
        if not isinstance(definition, dict):
            continue

        typ = definition.get('type')
        if not typ:
            continue

        convert_fun = _CONVERT_FUNS.get(typ)
        if convert_fun:
            if convert_fun(definition, f_out, indent_level + 1):
                _put(f_out, 0)
        elif typ not in unknown_types:
            unknown_types.add(typ)
            _logger.warn('Unknown type=%r', typ)


def main(argv=argv, stdin=stdin, stdout=stdout):
    basicConfig(
        level=WARN,
        format='[%(levelname)s] [%(funcName)s:%(lineno)d] %(message)s',
    )

    automatic_import_from = object()

    parser = ArgumentParser(prog=argv[0],
                            description='Convert C header to Cython .pxd file')
    parser.add_argument('input',
                        nargs='?',
                        default=None,
                        help='input file (.h or .json), default=<STDIN>')
    parser.add_argument('output',
                        nargs='?',
                        default=None,
                        help='output file (.pxd), default=<STDOUT>')
    parser.add_argument('-f', '--from',
                        nargs='?',
                        default=automatic_import_from,
                        dest='import_from',
                        help='Include path for of the header file. '
                             'If input file is present, then its basename '
                             'is used by default. If no parameter is '
                             'supplied or the input file is STDIN, then '
                             '"*" is used.')
    parser.add_argument('-t', '--type',
                        nargs=1,
                        choices=('auto', 'json', 'h'),
                        default='auto',
                        dest='input_type',
                        help='Input file type. '
                             '"json": output of ctypesgen. '
                             '"h": a header file '
                             '"auto": try to detect (default), either by '
                             'the extension of the input file, or by the '
                             'first character if the input.')
    parser.add_argument('-a', '--append',
                        default='w',
                        action='store_const',
                        const='a',
                        dest='write_mode',
                        help='Append to the end of the output file instead '
                             'of overwriting it.')
    parser.add_argument('-x', '--ctypesgen',
                        default=[],
                        action='append',
                        dest='ctypesgen_args',
                        help='Extra arguments for ctypesgen.py, '
                             'e.g. "-x=--all-headers".')
    parser.add_argument('--timeout',
                        nargs=1,
                        default=30.0,
                        type=float,
                        dest='ctypesgen_timeout',
                        help='Maximum runtime for ctypesgen.py, default=30.')

    args = parser.parse_args(argv[1:])

    if not args.input or args.input == '-':
        args.input = None

    if not args.output or args.output == '-':
        args.output = None

    if args.input_type == 'auto' and args.input:
        input_name, input_ext = splitext(args.input)
        if input_ext == '.json':
            args.input_type = 'json'
        elif input_ext == '.h':
            args.input_type = 'h'
    else:
        input_name = None
        input_ext = None

    if args.import_from is automatic_import_from:
        if input_name:
            input_name = basename(input_name)
            if input_ext == '.json':
                input_ext = '.h'
            args.import_from = '"%s%s"' % (input_name, input_ext)
        else:
            args.import_from = None

    if not args.input:
        input_data = stdin.read()
    else:
        with open(args.input, 'rb') as in_f:
            input_data = in_f.read()
    input_data = input_data.lstrip()

    if args.input_type == 'auto':
        args.input_type = 'json' if stdin[:1] == b'[' else 'h'

    if args.input_type == 'h':
        ctypesgen_process = None
        try:
            ctypesgen_process = Popen(
                ['ctypesgen.py',
                 *args.ctypesgen_args,
                 '--output-language=json',
                 '/dev/stdin'
                ],
                stdin=PIPE,
                stdout=PIPE,
                universal_newlines=isinstance(input_data, str),
            )
            input_data, _ = ctypesgen_process.communicate(
                input=input_data,
                timeout=args.ctypesgen_timeout,
            )
            if ctypesgen_process.returncode != 0:
                raise Exception('ctypesgen.py returned an error: %r',
                                ctypesgen_process.returncode)
        finally:
            if ctypesgen_process:
                ctypesgen_process.kill()

    if isinstance(input_data, bytes):
        input_data = input_data.decode('UTF-8')

    definitions = loads(input_data)

    with (open(args.output, args.write_mode)
          if args.output else stdout) as f_out:
        convert(definitions, f_out, args.import_from)


if __name__ == '__main__':
    main(argv, stdin, stdout)
