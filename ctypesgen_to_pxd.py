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
from builtins import compile as compile_expr
from inspect import stack, getframeinfo
from io import StringIO
from json import loads
from logging import getLogger, basicConfig, WARN
from os.path import basename, splitext
from regex import compile as compile_re, VERBOSE, VERSION1
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

automatic_import_from = object()


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
    int char short void size_t ssize_t float double
'''.split()) | set(_STDDEF_TYPES + _STDINT_TYPES)

_match_verbatim = compile_re(r'''
    # unary plus / minus
    (?>
        [+~-\s]+ |
        sizeof (?=\W)
    )*+

    (?>
        # match integer
        (?: 0x)?+
        \d++
        [uU]? [lL]{0,2}
    |
        # match float
        \d++ (?> [.] \d++)? (?> [eE] [+-]?+ \d++)?+
    |
        # match identifier
        [_a-zA-Z] [_a-zA-Z0-9]*+
    |
        # match string
        ["]
        (?>
            [^"\\\r\n]++ |
            [\\] (?>
                [abefnrtv\\'"?] |
                [1-2][0-7]{0,2} |
                [3-7][0-7]? |
                x[0-9a-fA-F]{2}
            )
        )*+
        ["]
    |
        # match parens
        \( (?R) \)
    )

    \s*+

    (?>
        # match calculation
        (?> [+*/%&^|-] | << | >>)
        (?R)
    )?+
''', VERBOSE | VERSION1).fullmatch

_match_repr_str = compile_re(r'''
    (['"])
    (?>
        (?! \1)
        (?>
            [^\\\r\n] |  # cannot add ++ because the (?!\1) is needed
            [\\] (?>
                [0abefnrtv\\'"?] |
                [1-2][0-7]{0,2} |
                [3-7][0-7]? |
                x[0-9a-fA-F]{2} |
                u[0-9a-fA-F]{4} |
                U[0-9a-fA-F]{8}
            )
        )
    )*+
    \1
''', VERBOSE | VERSION1).fullmatch

_match_int = compile_re(r'''
    \s*+
    (?>
        \( (?R) \)
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
''', VERBOSE | VERSION1).fullmatch

_match_identifier = compile_re(r'''
    \s*+
    (?>
        \( (?R) \)
    |
        (?P<value> [_a-zA-Z] [_a-zA-Z0-9]*+ )
    )
    \s*+
''', VERBOSE | VERSION1).fullmatch

_BinaryExpressionNode_Ops = {
    'addition': '+',
    'bitwise or': '|',
    'division': '/',
    'left shift': '<<',
    'less-than': '<',
    'multiplication': '*',
    'right shift': '>>',
    'subtraction': '-',
}

_UnaryExpressionNode_Ops = {
    'negation': '-',
}

_last_anon_enum = None, None  # FIXME: eliminate global variable


def _format_CtypesSimple(f_out, indent_level, ctype):
    name = ctype.get('name')
    if name in _SIMPLE_TYPES:
        signed = ctype.get('signed')
        longs = ctype.get('longs') or 0
        signedness = ('unsigned ' if not signed else
                      'signed ' if name in ('int', 'short') else
                      '')
        return (signedness, 'long ' * longs, name)

    _warn(f_out, indent_level, 'Unknown CtypesSimple name=%r', name)
    return False


def _convert_constant(f_out, indent_level, definition):
    name = definition.get('name')
    value = definition.get('value')

    if not name or not value:
        _warn(f_out, indent_level,
              'Unknown constant name=%r value=%r', name, value)
        return False

    _anon_enum_name, _anon_enum_fields = _last_anon_enum
    if _anon_enum_name and name in _anon_enum_fields:
        _put(f_out, indent_level,
             'cdef enum:  # was anonymous enum: ', _anon_enum_name)
        _put(f_out, indent_level + 1, name, ' = ', _anon_enum_name, '.', name)
        return True

    const_args = _format_constant(f_out, indent_level, value)
    if not const_args:
        return const_args

    _put(f_out, indent_level, 'cdef enum:  # was a constant')
    _put(f_out, indent_level + 1, name, ' = (', *const_args, ')')
    return True


def _format_rhs_ConstantExpressionNode(f_out, indent_level, definition):
    value = definition.get('value')
    if value is not None:
        return value,


def _format_rhs_BinaryExpressionNode(f_out, indent_level, definition):
    name = definition.get('name')
    if not name:
        _logger.error('Unknown BinaryExpressionNode name=%r', name)
        return False

    left = definition.get('left')
    right = definition.get('right')
    if not all(isinstance(i, dict) for i in (left, right)):
        _logger.error('Unknown BinaryExpressionNode type(left)=%r '
                      'type(right)=%r', type(left), type(right))
        return False

    left_args = _format_rhs(f_out, indent_level, left)
    if not left_args:
        return left_args

    right_args = _format_rhs(f_out, indent_level, right)
    if not right_args:
        return right_args

    op = _BinaryExpressionNode_Ops.get(name)
    if op:
        return ('((', *left_args, ') ', op, ' (', *right_args, '))')

    _warn(f_out, indent_level,
          'Unsupported BinaryExpressionNode name=%r', name)
    return False


def _format_rhs_UnaryExpressionNode(f_out, indent_level, definition):
    name = definition.get('name')
    child = definition.get('child')
    if not name or not isinstance(child, dict):
        _logger.error('Unknown UnaryExpressionNode name=%r type(child)=%r',
                      name, type(child))
        return False

    child_args = _format_rhs(f_out, indent_level, child)
    if not child_args:
        return child_args

    op = _UnaryExpressionNode_Ops.get(name)
    if op:
        return ('(', op, ' (', *child_args, '))')

    _warn(f_out, indent_level,
          'Unsupported UnaryExpressionNode name=%r', name)
    return False


def _format_rhs_IdentifierExpressionNode(f_out, indent_level, definition):
    name = definition.get('name')
    if not name:
        _logger.error('Unknown IdentifierExpressionNode name=%r', name)
        return False

    return name,


def _format_rhs_SizeOfExpressionNode(f_out, indent_level, definition):
    child = definition.get('child')
    if not isinstance(child, dict):
        _logger.error('Unknown SizeOfExpressionNode type(child)=%r',
                      type(child))
        return False

    child_args = _convert_base_Klass(f_out, indent_level, child)
    if not child_args:
        return child_args

    return ('(sizeof(', *child_args, '))')


def _format_rhs_ConditionalExpressionNode(f_out, indent_level, definition):
    cond = definition.get('cond')
    no = definition.get('no')
    yes = definition.get('yes')
    if any(not isinstance(i, dict) for i in (cond, no, yes)):
        _logger.error('Unknown ConditionalExpressionNode type(cond)=%r '
                      'type(no)=%r type(yes)=%r',
                      type(cond), type(no), type(yes))
        return False

    cond_args = _format_rhs(f_out, indent_level, cond)
    if not cond_args:
        return cond_args

    no_args = _format_rhs(f_out, indent_level, no)
    if not no_args:
        return no_args

    yes_args = _format_rhs(f_out, indent_level, yes)
    if not yes_args:
        return yes_args

    return ('((', *yes_args, ') if (',
            *cond_args, ') else (', *no_args, '))')


def _format_rhs_TypeCastExpressionNode(f_out, indent_level, definition):
    ctype = definition.get('ctype')
    base = definition.get('base')
    if any(not isinstance(i, dict) for i in (ctype, base)):
        _logger.error('Unknown TypeCastExpressionNode type(ctype)=%r '
                      'type(base)=%r', type(ctype), type(base))
        return False

    type_args = _convert_base_Klass(f_out, indent_level, ctype)
    if not type_args:
        return type_args

    base_args = _format_rhs(f_out, indent_level, base)
    if not base_args:
        return base_args

    return ('(<', *type_args, '> (', *base_args, '))')


_FORMAT_RHS_FUNS = {
    'BinaryExpressionNode': _format_rhs_BinaryExpressionNode,
    'ConditionalExpressionNode': _format_rhs_ConditionalExpressionNode,
    'ConstantExpressionNode': _format_rhs_ConstantExpressionNode,
    'IdentifierExpressionNode': _format_rhs_IdentifierExpressionNode,
    'SizeOfExpressionNode': _format_rhs_SizeOfExpressionNode,
    'TypeCastExpressionNode': _format_rhs_TypeCastExpressionNode,
    'UnaryExpressionNode': _format_rhs_UnaryExpressionNode,
}


def _format_rhs(f_out, indent_level, definition):
    klass = definition.get('Klass')
    if not klass:
        _logger.error('Unknown rhs Klass=%r', klass)
        return False

    convert_fun = _FORMAT_RHS_FUNS.get(klass)
    if convert_fun:
        return convert_fun(f_out, indent_level, definition)

    _warn(f_out, indent_level, 'Unsupported rhs Klass=%r', klass)
    return False


def _convert_enum(f_out, indent_level, definition):
    global _last_anon_enum

    name = definition.get('name')
    fields = definition.get('fields')
    if not name or not isinstance(fields, (list, NoneType)):
        _logger.error('Unknown enum name=%r type(fields)=%r',
                      name, type(fields))
        return False

    if name.startswith('anon_'):
        _last_anon_enum = name, {field.get('name') for field in fields}

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

            value_args = _format_rhs(f_out, indent_level, ctype)
            if not value_args:
                continue

            if not printed:
                _put(f_out, indent_level, 'cdef enum ', name or '', ':')
                printed = True

            _put(f_out, indent_level + 1, field_name, ' = (', *value_args, ')')

    return printed


def _format_function(f_out, indent_level, definition):
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
        returns_args = _convert_base_Klass(f_out, indent_level, returns)
        if not returns_args:
            return returns_args
    else:
        returns_args = 'void',

    args_args = ()
    if args:
        for i in args:
            i_args = _convert_base_Klass(f_out, indent_level, i)
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


def _convert_function(f_out, indent_level, definition):
    name = definition.get('name')
    if not name:
        _logger.error('Unknown fuction name=%r', name)
        return False

    args = _format_function(f_out, indent_level, definition)
    if not args:
        return args

    returns_args, args_args = args

    _put(f_out, indent_level,
         'cdef ', *returns_args, ' ', name, '(', *args_args, ')')
    return True


def _format_constant(f_out, indent_level, value):
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
        return result,

    m = _match_identifier(value)
    if m:
        result, = m.capturesdict().get('value')
        return result,

    m = _match_repr_str(value)
    if m:
        return value,

    m = _match_verbatim(value)
    if m:
        _logger.info('Copying macro definition verbatim: %r', value)
        return value,

    _warn(f_out, indent_level,
          'Constant expression not understood: %r', value)
    return False


def _convert_macro(f_out, indent_level, definition):
    name = definition.get('name')
    value = definition.get('value')

    if name == value:
        _logger.info('Macro omitted: %s', name)
        return False  # sic

    const_args = _format_constant(f_out, indent_level, value)
    if not const_args:
        return const_args

    _put(f_out, indent_level, 'cdef enum:  # was a macro: %r' % value)
    _put(f_out, indent_level + 1, name, ' = (', *const_args, ')')
    return True


def _convert_variable(f_out, indent_level, definition):
    name = definition.get('name')
    if not name:
        _logger.error('Unknown variable name=%r', name)
        return False

    ctype = definition.get('ctype')
    if not isinstance(ctype, dict):
        _logger.error('Unknown variable type(ctype)=%r', type(ctype))
        return False

    type_args = _convert_base_Klass(f_out, indent_level, ctype)
    if not type_args:
        return type_args

    _put(f_out, indent_level, 'cdef extern ', *type_args, ' ', name)
    return True


def _convert_struct(f_out, indent_level, definition, struct='struct'):
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
            _convert_typedef(f_out, indent_level + 1, field,
                             include_cdef=False)

    return True


def _convert_union(f_out, indent_level, definition):
    return _convert_struct(f_out, indent_level, definition, struct='union')


def _convert_typedef_CtypesSimple(f_out, indent_level,
                                  name, ctype, include_cdef):
    args = _format_CtypesSimple(f_out, indent_level, ctype)
    if not args:
        return args

    return (
        'ctypedef ' if include_cdef else '',
        *args, ' ', name or '',
    )


def _convert_typedef_CtypesEnum(f_out, indent_level,
                                name, ctype, include_cdef):
    tag = ctype.get('tag')
    if not tag:
        _logger.error('Unknown CtypesStruct tag=%r', tag)
        return False

    return ('ctypedef ' if include_cdef else '', tag, ' ', name or '')


def _convert_typedef_CtypesBitfield(f_out, indent_level,
                                    name, ctype, include_cdef):
    base = ctype.get('base')
    if not isinstance(base, dict):
        _logger.error('Unknown CtypesBitfield type(base)=%r', type(base))
        return False

    klass = base.get('Klass')
    if not klass:
        _logger.error('Unknown CtypesBitfield Klass=%r', klass)
        return False

    convert_fun = _CONVERT_TYPEDEF_FUNS.get(klass)
    if convert_fun:
        _warn(f_out, indent_level, 'Bitfield specification ignored in .pxd')
        return convert_fun(f_out, indent_level, name, base, include_cdef)

    _warn(f_out, indent_level, 'Unsupported CtypesBitfield Klass=%r', klass)
    return False


def _convert_typedef_CtypesStruct(f_out, indent_level,
                                  name, ctype, include_cdef):
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

    return ('ctypedef ' if include_cdef else '', *name_args, ' ', name or '')


def _convert_base_CtypesSimple(f_out, indent_level, base):
    return _format_CtypesSimple(f_out, indent_level, base)


def _convert_base_CtypesStruct(f_out, indent_level, base):
    tag = base.get('tag')
    variety = base.get('variety')
    if not tag or not variety:
        _logger.error('Unsupported base Klass=%r tag=%r variety=%r',
                      klass, tag, variety)
        return False

    return _anon_struct_name(variety, tag)


def _convert_base_CtypesPointer(f_out, indent_level, base):
    destination = base.get('destination')
    if not isinstance(destination, dict):
        _logger.error('Unsupported base Klass type(destination)=%r',
                      destination)
        return False

    args = _convert_base_Klass(f_out, indent_level, destination)
    if not args:
        return args

    return (*args, '*')


def _convert_base_CtypesTypedef(f_out, indent_level, base):
    name = base.get('name')
    if not name:
        _logger.error('Unsupported base Klass CtypesTypedef name=%r', name)
        return False

    return (name or identifier,)


def _convert_base_CtypesFunction(f_out, indent_level, base):
    args = _format_function(f_out, indent_level, base)
    if not args:
        return args

    returns_args, args_args = args

    return (*returns_args, '(', *args_args, ')')


def _convert_base_CtypesArray(f_out, indent_level, base):
    args = _format_CtypesArray(f_out, indent_level, base)
    if not args:
        return args

    base_args, count_args = args
    return (*base_args, '[', *count_args, ']')


def _convert_base_CtypesSpecial(f_out, indent_level, base):
    name = base.get('name')
    if not name:
        _logger.error('Unsupported base Klass CtypesSpecial name=%r', name)

    if name == 'String':
        return 'char*',

    _warn(f_out, indent_level, 'Unknown CtypesSpecial name=%r', name)
    return False


def _convert_base_Klass(f_out, indent_level, base):
    klass = base.get('Klass')
    convert_fun = _CONVERT_BASE_FUNS.get(klass)
    if convert_fun:
        return convert_fun(f_out, indent_level, base)
    else:
        _warn(f_out, indent_level, 'Unsupported base Klass=%r', klass)
        return False


def _format_CtypesArray(f_out, indent_level, ctype):
    base = ctype.get('base')
    if not isinstance(base, dict):
        _logger.error('CtypesArray type(base)=%r', type(base))
        return False

    count = ctype.get('count')
    if not isinstance(count, (dict, NoneType)):
        _logger.error('CtypesArray type(count)=%r', type(count))
        return False

    if count is not None:
        count_args = _format_rhs(f_out, indent_level, count)
        if not count_args:
            return count_args
    else:
        count_args = ()

    base_args = _convert_base_Klass(f_out, indent_level, base)
    if not base_args:
        return base_args

    return base_args, count_args


def _convert_typedef_CtypesArray(f_out, indent_level,
                                 name, ctype, include_cdef):
    args = _format_CtypesArray(f_out, indent_level, ctype)
    if not args:
        return args

    base_args, count_args = args
    return (
        'ctypedef ' if include_cdef else '',
        *base_args, ' ', name or '', '[', *count_args, ']',
    )


def _convert_typedef_CtypesSpecial(f_out, indent_level,
                                   name, ctype, include_cdef):
    special_name = ctype.get('name')
    if not special_name:
        _logger.error('Unknown CtypesSpecial name=%r', special_name)
        return False

    if special_name == 'String':
        return (
            'ctypedef ' if include_cdef else '',
            'char* ', name or '',
        )

    _warn(f_out, indent_level,
          'Unsupported CtypesSpecial name=%r', special_name)
    return False


def _convert_typedef_CtypesPointer(f_out, indent_level,
                                   name, ctype, include_cdef):
    destination = ctype.get('destination')
    if not isinstance(destination, dict):
        _logger.error('Unsupported base Klass type(destination)=%r',
                      destination)
        return False

    args = _convert_base_Klass(f_out, indent_level, destination)
    if not args:
        return args

    return (
        'ctypedef ' if include_cdef else '',
        *args, '* ', name or '',
    )


def _convert_typedef_CtypesFunction(f_out, indent_level,
                                    name, ctype, include_cdef):
    args = _format_function(f_out, indent_level, ctype)
    if not args:
        return args

    returns_args, args_args = args
    return (
        'ctypedef ' if include_cdef else '',
        *returns_args, ' ', name or '', '(', *args_args, ')',
    )


def _convert_typedef_CtypesTypedef(f_out, indent_level,
                                   name, ctype, include_cdef):
    base_name = ctype.get('name')
    if not base_name:
        _logger.error('Unknown CtypesTypedef name=%r', base_name)
        return False

    return (
        'ctypedef ' if include_cdef else '',
        base_name, ' ', name or '',
    )


def _convert_typedef(f_out, indent_level, definition, include_cdef=True):
    name = definition.get('name')
    if not name and include_cdef:
        _logger.error('Unknown typedef data name=%r', name)
        return False

    ctype = definition.get('ctype')
    if not isinstance(ctype, dict):
        _logger.error('Unknown typedef data type(ctype)=%r', type(ctype))
        return False

    klass = ctype.get('Klass')
    convert_fun = _CONVERT_TYPEDEF_FUNS.get(klass)
    if convert_fun:
        args = convert_fun(f_out, indent_level, name, ctype, include_cdef)
        if not args:
            return args

        _put(f_out, indent_level, *args)
        return True

    else:
        _warn(f_out, indent_level, 'Unknown typedef Klass=%r', klass)


def _convert_macro_function(f_out, indent_level, definition,
                            include_cdef=True):
    name = definition.get('name')
    args = definition.get('args') or ()
    body = definition.get('body') or ''
    if not name:
        _logger.error('Unknown macro function name=%r', name)
        return False

    _warn(f_out, indent_level,
          'Unconvertable macro function: %s(%s) %r',
          name, ', '.join(args), body)
    return True


def _put(f_out, indent_level, *args, **kw):
    print('    ' * indent_level, *args, file=f_out, sep='')


def _warn(f_out, indent_level, warn_format, *args):
    caller = getframeinfo(stack()[1][0])

    buf = StringIO()
    print('[%s:%d]' % (caller.function, caller.lineno),
          warn_format % args, file=buf, end='')
    buf.seek(0)

    msg = buf.read()
    _logger.warn(msg)
    _put(f_out, indent_level, '# ', msg)


def convert(definitions, f_out, *,
            import_from='*', indent_level=0, def_extras=(),
            include_std_types=True):
    global _last_anon_enum

    if include_std_types:
        for h_name, items in (('stddef', _STDDEF_TYPES),
                              ('stdint', _STDINT_TYPES)):
            _put(f_out, indent_level, 'from libc.', h_name, ' cimport (')
            for line in wrap(', '.join(items), 79 - 4 * (indent_level + 1)):
                _put(f_out, indent_level + 1, line)
            _put(f_out, indent_level, ')')

        _put(f_out, indent_level)
        _put(f_out, indent_level)

    _put(f_out, indent_level,
         'cdef extern from ', import_from or '*', *def_extras, ':')

    unknown_types = set()
    for definition in definitions:
        if not isinstance(definition, dict):
            continue

        typ = definition.get('type')
        if not typ:
            _logger.error('Unknown type=%r', typ)
            continue

        if typ != 'constant':
            _last_anon_enum = None, None

        convert_fun = _CONVERT_FUNS.get(typ)
        if convert_fun:
            if convert_fun(f_out, indent_level + 1, definition):
                _put(f_out, 0)

        elif typ not in unknown_types:
            unknown_types.add(typ)
            _warn(f_out, indent_level, 'Unknown type=%r', typ)


def gen_argv_parser(prog):
    parser = ArgumentParser(prog=prog,
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
    parser.add_argument('--gil',
                        action='store_const',
                        default=' nogil',
                        const='',
                        dest='use_gil',
                        help='Don\'t release global interpreter lock.')
    parser.add_argument('--indent_level',
                        nargs=1,
                        type=int,
                        default=0,
                        dest='indent_level',
                        help='Starting indentation level, default=0.')
    parser.add_argument('-q', '--quiet',
                        action='store_const',
                        default=False,
                        const=True,
                        dest='quiet_ctypesgen',
                        help='Suppress ctypesgen warnings.')
    parser.add_argument('--no-includes',
                        action='store_const',
                        default=False,
                        const=True,
                        dest='no_includes',
                        help='Don\'t import standard types like int32_t.')
    return parser


def main(argv=argv, stdin=stdin, stdout=stdout):
    basicConfig(
        level=WARN,
        format='[%(levelname)s] %(message)s',
    )

    parser = gen_argv_parser(argv[0])
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
                 '/dev/stdin'],
                stdin=PIPE,
                stdout=PIPE,
                stderr=PIPE if args.quiet_ctypesgen else stderr,
                universal_newlines=isinstance(input_data, str),
            )
            input_data, error_log = ctypesgen_process.communicate(
                input=input_data,
                timeout=args.ctypesgen_timeout,
            )
            if ctypesgen_process.returncode != 0:
                if args.quiet_ctypesgen:
                    stderr.write(error_log)
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
        convert(definitions, f_out,
                import_from=args.import_from,
                indent_level=args.indent_level,
                def_extras=(args.use_gil,),
                include_std_types=not args.no_includes)


_CONVERT_BASE_FUNS = {
    'CtypesArray': _convert_base_CtypesArray,
    'CtypesFunction': _convert_base_CtypesFunction,
    'CtypesPointer': _convert_base_CtypesPointer,
    'CtypesSimple': _convert_base_CtypesSimple,
    'CtypesSpecial': _convert_base_CtypesSpecial,
    'CtypesStruct': _convert_base_CtypesStruct,
    'CtypesTypedef': _convert_base_CtypesTypedef,
}

_CONVERT_TYPEDEF_FUNS = {
    'CtypesArray': _convert_typedef_CtypesArray,
    'CtypesBitfield': _convert_typedef_CtypesBitfield,
    'CtypesEnum': _convert_typedef_CtypesEnum,
    'CtypesFunction': _convert_typedef_CtypesFunction,
    'CtypesPointer': _convert_typedef_CtypesPointer,
    'CtypesSimple': _convert_typedef_CtypesSimple,
    'CtypesStruct': _convert_typedef_CtypesStruct,
    'CtypesTypedef': _convert_typedef_CtypesTypedef,
    'CtypesSpecial': _convert_typedef_CtypesSpecial,
}

_CONVERT_FUNS = {
    'constant': _convert_constant,
    'enum': _convert_enum,
    'function': _convert_function,
    'macro': _convert_macro,
    'macro_function': _convert_macro_function,
    'struct': _convert_struct,
    'typedef': _convert_typedef,
    'union': _convert_union,
    'variable': _convert_variable,
}


if __name__ == '__main__':
    main(argv, stdin, stdout)
