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


from json import load
from logging import getLogger, basicConfig
from re import match
from sys import stdin, stdout, argv


__author__ = 'René Kijewski  <rene.SURNAME@fu-berlin.de>'
__copyright__ = 'Copyright 2016 Freie Universität Berlin'
__credits__ = ['René Kijewski']
__license__ = 'Apache License, Version 2.0'
__version__ = '0.0.1'
__maintainer__ = 'René Kijewski'
__email__ = 'rene.SURNAME@fu-berlin.de'
__status__ = 'Production'


__all__ = ('convert',)

_logger = getLogger('ctypes_to_pxd')

NoneType = type(None)


def _anon_struct_name(variety, tag):
    return tag


_SIMPLE_TYPES = frozenset('''int char short void size_t'''.split())


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
            _logger.warn('Unknown BinaryExpressionNode type(left)=%r '
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
            _logger.warn('Unknown IdentifierExpressionNode name=%r', name)

        return name,

    _logger.warn('Unsupported rhs Klass=%r', klass)
    return False


def _convert_enum(definition, f_out, indent_level):
    name = definition.get('name')
    fields = definition.get('fields')

    if not name or not isinstance(fields, (list, NoneType)):
        _logger.warn('Unknown enum name=%r type(fields)=%r',
                     name, type(fields))
        return False

    printed = None
    if fields:
        for field in fields:
            field_name = field.get('name')
            if not field_name:
                _logger.warn('Unknown enum field name=%r', field_name)
                continue

            ctype = field.get('ctype')
            if not isinstance(ctype, dict):
                _logger.warn('Unknown enum field ctype=%r', ctype)
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
    variadic = definition.get('variadic')
    returns = definition.get('return')
    args = definition.get('args')

    if not isinstance(returns, (dict, NoneType)):
        _logger.warn('Unknown fuction type(return)=%r', type(returns))
        return False
    elif not isinstance(args, (list, NoneType)):
        _logger.warn('Unknown fuction type(args)=%r', type(returns))
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
        _logger.warn('Unknown fuction name=%r', name)
        return False

    args = _format_function(definition)
    if not args:
        return args

    returns_args, args_args = args

    _put(f_out, indent_level,
         'cdef ', *returns_args, ' ', name, '(', *args_args, ')')
    return True


def _convert_macro(definition, f_out, indent_level):
    name = definition.get('name')
    value = definition.get('value')

    if name == value:
        return False  # sic

    elif isinstance(value, str):
        m = match(
            r'\A\s*(?:'
                r'(?:\('
                    r'(?:(-)|[+])?\s*'  # 1
                    r'(\0x?)?'          # 2
                    r'(\d+)'            # 3
                r'\))'
            r'|'
                r'(?:'
                    r'(?:(-)|[+])?\s*'  # 4
                    r'(\0x?)?'          # 5
                    r'(\d+)'            # 6
                r')'
            ')\s*\Z',
            value,
        )
        if m:
            minus  = m.group(1) or m.group(4)
            prefix = m.group(2) or m.group(5)
            digits = m.group(3) or m.group(6)

            value = int(digits, (10 if not prefix else
                                 16 if prefix == '0x' else 0))
            if minus:
                value = -1

            _put(f_out, indent_level, 'cdef enum:  # was a macro')
            _put(f_out, indent_level + 1, name, ' = (', value, ')')
            return True

    _logger.warn('Macro omitted: %s = %r', name, value)
    return False


def _convert_struct(definition, f_out, indent_level, struct='struct'):
    name = definition.get('name')
    fields = definition.get('fields')
    if not name or not isinstance(fields, (list, NoneType)):
        _logger.warn('Unknown %s data name=%r type(ctype)=%r',
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
        _logger.warn('Unknown CtypesStruct variety=%r', variety)
        return False

    tag = ctype.get('tag')
    if not tag:
        _logger.warn('Unknown CtypesStruct tag=%r', tag)
        return False

    name_args = _anon_struct_name(variety, tag)
    if not name_args:
        return name_args

    if name_args == _anon_struct_name(variety, name):
        return False  # sic

    return ('ctypedef ' if include_cdef else '', *name_args, ' ', name)


def _convert_base_Klass(base):
    klass = base.get('Klass')
    if klass == 'CtypesSimple':
        return _format_CtypesSimple(base)

    elif klass == 'CtypesStruct':
        tag = base.get('tag')
        variety = base.get('variety')
        if not tag or not variety:
            _logger.warn('Unsupported base Klass=%r tag=%r variety=%r',
                         klass, tag, variety)
            return False

        return _anon_struct_name(variety, tag)

    elif klass == 'CtypesPointer':
        destination = base.get('destination')
        if not isinstance(destination, dict):
            _logger.warn('Unsupported base Klass type(destination)=%r',
                         destination)
            return False

        args = _convert_base_Klass(destination)
        if not args:
            return args

        return (*args, '*')

    elif klass == 'CtypesTypedef':
        name = base.get('name')
        if not name:
            _logger.warn('Unsupported base Klass CtypesTypedef name=%r', name)
            return False

        return (name or identifier,)

    elif klass == 'CtypesFunction':
        args = _format_function(base)
        if not args:
            return args

        returns_args, args_args = args

        return (*returns_args, '(', *args_args, ')')

    else:
        _logger.warn('Unsupported base Klass=%r', klass)
        return False


def _convert_typedef_CtypesArray(name, ctype, include_cdef):
    base = ctype.get('base')
    count = ctype.get('count')
    if not all(isinstance(i, dict) for i in (base, count)):
        _logger.warn('CtypesArray type(base)=%r type(count)=%r',
                     type(base), type(count))
        return False

    count_args = _format_rhs(count)
    if not count_args:
        return count_args

    args = _convert_base_Klass(base)
    if not args:
        return args

    return (
        'ctypedef ' if include_cdef else '',
        *args, ' ', name or '', '[', *count_args, ']',
    )


def _convert_typedef_CtypesPointer(name, ctype, include_cdef):
    destination = ctype.get('destination')
    if not isinstance(destination, dict):
        _logger.warn('Unsupported base Klass type(destination)=%r',
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
        'cdef ' if include_cdef else '',
        *returns_args, ' (*', name, ')(', *args_args, ')',
    )


def _convert_typedef_CtypesTypedef(name, ctype, include_cdef):
    base_name = ctype.get('name')
    if not base_name:
        _logger.warn('Unknown CtypesTypedef name=%r', base_name)
        return False

    return (
        'cdef ' if include_cdef else '',
        base_name, ' ', name,
    )


_CONVERT_TYPEDEF_FUNS = {
    'CtypesSimple': _convert_typedef_CtypesSimple,
    'CtypesStruct': _convert_typedef_CtypesStruct,
    'CtypesArray': _convert_typedef_CtypesArray,
    'CtypesPointer': _convert_typedef_CtypesPointer,
    'CtypesFunction': _convert_typedef_CtypesFunction,
    'CtypesTypedef': _convert_typedef_CtypesTypedef,
}


def _convert_typedef(definition, f_out, indent_level, include_cdef=True):
    name = definition.get('name')
    ctype = definition.get('ctype')
    if not name or not isinstance(ctype, dict):
        _logger.warn('Unknown typedef data name=%r type(ctype)=%r',
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


_CONVERT_FUNS = {
    'constant': _convert_constant,
    'enum': _convert_enum,
    'function': _convert_function,
    'macro': _convert_macro,
    'struct': _convert_struct,
    'typedef': _convert_typedef,
    'union': _convert_union,
}


def _put(f_out, indent_level, *args, **kw):
    print('    ' * indent_level, *args, file=f_out, sep='')


def convert(f_in, f_out, import_from='*', indent_level=0):
    unknown_types = set()

    _put(f_out, indent_level,
         'cdef extern from ', import_from or '*', ' nogil:')
    indent_level += 1

    if hasattr(f_in, 'read'):
        definition = load(f_in)
    else:
        definition = fin

    for definition in definition:
        if not isinstance(definition, dict):
            continue

        typ = definition.get('type')
        if not typ:
            continue

        convert_fun = _CONVERT_FUNS.get(typ)
        if convert_fun:
            if convert_fun(definition, f_out, indent_level):
                _put(f_out, 0)
        elif typ not in unknown_types:
            unknown_types.add(typ)
            _logger.warn('Unknown type=%r', typ)


if __name__ == '__main__':
    basicConfig()
    convert(stdin, stdout, (len(argv) > 1) and ("'%s'" % argv[1]))
