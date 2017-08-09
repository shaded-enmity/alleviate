import sys
import os
import exceptions
import errno
import inspect
import traceback
import pprint
import ast
import astdump

from difflib import SequenceMatcher
from stat import S_IMODE
from pwd import getpwuid
from grp import getgrgid

from alleviate.disas import get_function_and_args


class Output:
    Plain='plain'
    Detailed='detailed'
    JSON='json'


_SIMILARITY_CUTOFF = 75


class Alleviation:
    def __init__(self):
        pass

    def match(self, exception):
        raise NotImplementedError

    def run(self, output):
        raise NotImplementedError


class Description:
    def __init__(self, text):
        self.text = text


class Solution:
    def __init__(self, desc, sol):
        self.solution = sol
        self.description = desc


class Symptom:
    def __init__(self, name, value):
        self.name = name
        self.value = value


class Header:
    def __init__(self, title):
        self.title = title


class Separator:
    def __init__(self, num=0):
        self.num = num


class AlleviateContext:
    def __init__(self, allev, fmt, exc):
        self.items = []
        self.alleviation = allev
        self.format = fmt
        self.exception = exc

    def add_item(self, item):
        self.items.append(item)


class ItemRender:
    def __init__(self):
        self.indent = 1
        self.column = 0

    @property
    def i(self):
        return '   ' * self.indent

    def render_plain_description(self, item):
        return item.text + '\n'

    def render_detailed_description(self, item):
        return item.text + '\n'

    def render_json_description(self, item):
        return item.text

    def render_plain_solution(self, item):
        return item.solution + '\n'

    def render_detailed_solution(self, item):
        return self.i + item.description + ":\n\n" + item.solution + '\n'

    def render_json_solution(self, item):
        return item.solution

    def render_plain_symptom(self, item):
        return ''

    def render_detailed_symptom(self, item):
        return self.i + '{}:{}{}\n'.format(item.name, ' ' * (1 + len(item.name) - self.column), item.value)

    def render_json_symptom(self, item):
        return {'name': item.name, 'value': item.value}

    def render_plain_header(self, item):
        return item.title + '\n'

    def render_detailed_header(self, item):
        return item.title + '\n' + ('-'*len(item.title)) + '\n'

    def render_json_header(self, item):
        return ''

    def render_plain_separator(self, item):
        return '' + ('\n' * item.num)

    def render_detailed_separator(self, item):
        return '' + ('\n' * item.num)

    def render_json_separator(self, item):
        return ''


class Renderer:
    def __init__(self, context):
        self.item_render = ItemRender()
        self.context = context

    def _render(self, item):
        class_name = item.__class__.__name__.lower()
        meth = 'render_' + self.context.format + '_' + class_name
        return getattr(self.item_render, meth)(item)

    def render(self):
        meth = 'render_' + self.context.format
        return getattr(self, meth)()

    def render_plain(self):
        s = ''
        for item in self.context.items:
            s+=self._render(item) + '\n'
        return s

    def render_detailed(self):
        def _by_type(typ):
            return list(filter(lambda x: isinstance(x, typ), self.context.items))

        max_name_length = max((len(s.name) for s in _by_type(Symptom)))
        self.item_render.column = max_name_length
        s = ''
        for item in self.context.items:
            s+=self._render(item) + '\n'
        return s

    def render_json(self):
        def _by_type(typ):
            return list(filter(lambda x: isinstance(x, typ), self.context.items))

        import json
        return json.dumps({
            'description': _by_type(Description)[0].text.strip(),
            'exception': type(self.context.exception).__name__,
            'message': self.context.exception.message,
            'symptoms': [self._render(s) for s in _by_type(Symptom)],
            'solutions': [self._render(s).strip() for s in _by_type(Solution)]
        }, indent=3)



class GetNodesAtLineVisitor(ast.NodeVisitor):
    def __init__(self, line):
        self._matches = []
        self._line = line
        ast.NodeVisitor.__init__(self)

    def generic_visit(self, node):
        if hasattr(node, 'lineno') and node.lineno == self._line:
            self.matches.append(node)
        ast.NodeVisitor.generic_visit(self, node)

    @property
    def matches(self):
        return self._matches


def _defer_open_mode(call):
    kwargs = {kwarg.arg: kwarg.value for kwarg in call.keywords}
    if 'mode' in kwargs:
        return kwargs['mode'].s
    return call.args[1].s if len(call.args) > 1 else None


def _find_call(nodes, name):
    for node in nodes:
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name):
                if func.id == name:
                    return node


def _find_similar_files(path):
    if not os.path.isabs(path):
        path = os.path.join(os.getcwd(), path)

    base = os.path.dirname(path)
    file = os.path.basename(path)
    scores = []

    for f in os.listdir(base):
        scores.append((os.path.join(base, f), int(SequenceMatcher(None, f, file).ratio()*100)))

    by_score = lambda (_, b): b
    by_score_cutoff = lambda (_, b): b > _SIMILARITY_CUTOFF

    return list(filter(by_score_cutoff, sorted(scores, key=by_score)))


def _tail_frame(tb):
    while tb.tb_next:
        tb = tb.tb_next
    return tb.tb_frame


def _get_function_name(tb, frame):
    special_call = get_function_and_args(frame.f_code, tb.tb_lasti, frame)
    if special_call:
        return special_call[0]
    else:
        first_self = frame.f_code.co_argcount > 0 and 'self' in frame.f_code.co_varnames
        full_name = frame.f_code.co_name + '()'
        if first_self:
            full_name = type(frame.f_locals['self']).__name__ + '.' + full_name
        return full_name


def get_function_name(tb):
    frame = _tail_frame(tb)
    return _get_function_name(tb, frame)


class ErrnoAlleviation(Alleviation):
    @property 
    def errnos(self):
        return []
    
    def match(self, exception):
        #print(exception.errno, self.errnos, self.__class__)
        return hasattr(exception, 'errno') and exception.errno in self.errnos


def _get_file_ownership(stat, numeric=True):
    if numeric:
        return (stat.st_uid, stat.st_gid)
    return (
        getpwuid(stat.st_uid).pw_name,
        getgrgid(stat.st_gid).gr_name
    )


def _get_mode_description(mode, stat, path):

    X, W, R = 1, 2, 4

    # 011
    rw = 'readable and writable by {}'
    # 001
    r = 'readable by {}'
    # 010
    w = 'writable by {}'
    # 100
    x = 'executable by {}'
    # 101
    rx = 'readable and executable by {}'
    # 110
    wx = 'writable and executable by {}'
    # 111
    rwx = 'writable, readable and executable by {}'

    user, group, other = (mode >> 6 & 7, mode >> 3 & 7, mode & 7)

    all_read  = R & user == R and R & group == R and R & other == R
    all_write = W & user == W and W & group == W and W & other == W
    all_exec  = X & user == X and X & group == X and X & other == X

    owner, group = _get_file_ownership(stat)
    uid, gids = os.getuid(), os.getgroups()

    all_wrong = owner != uid and group not in gids

    is_dir = os.path.isdir(path)
    parent_dir = os.path.dirname(path) if not is_dir else os.path.split(path)[0]


class Eperm(ErrnoAlleviation):
    @property
    def errnos(self):
        return [errno.EPERM, errno.EACCES]

    def run(self, exception, output):
        C = AlleviateContext(self, output, exception)
        R = Renderer(C)

        _, _, tb = sys.exc_info()
        filename = exception.filename

        frame = _tail_frame(tb)
        fname = _get_function_name(tb, frame)

        if not os.path.isabs(filename):
            filename = os.path.join(os.getcwd(), filename)

        mode = None
        try:
            mode = S_IMODE(os.stat(filename).st_mode)
        except:
            C.add_item(Description('Unable to stat {}'.format(filename)))
            return

        _get_mode_description(mode, filename)
        errno_ = exception.errno


class Enoent(ErrnoAlleviation):
    @property
    def errnos(self):
        return [errno.ENOENT]

    def run(self, exception, output):
        C = AlleviateContext(self, output, exception)
        R = Renderer(C)

        _, _, tb = sys.exc_info()
        filename = exception.filename

        similar = _find_similar_files(exception.filename)
        similars = ''
        if similar:
            for sf in similar[:3]:
                similars += '      {0} similarity: {1:d}%\n'.format(*sf)

        fname = get_function_name(tb)

        if not os.path.isabs(filename):
            filename = os.path.join(os.getcwd(), filename)

        errno_ = exception.errno

        C.add_item(Header('Program error'))
        C.add_item(Description('File {} could not be found\n\nErrno:  {} ({})\nAction: {}'.format(
            filename, errno_, errno.errorcode[errno_], fname
            )))
        C.add_item(Separator())
        C.add_item(Header('Symptoms'))
        C.add_item(Symptom('File does not exist', filename))
        C.add_item(Separator())
        C.add_item(Header('Solutions'))
        C.add_item(Solution('Check out files with similar name', similars))

        print(R.render())
    

__alleviations = [
    Enoent(), Eperm()
]


def find_alleviation(exc):
    for alleviation in __alleviations:
        if alleviation.match(exc):
            return alleviation


def exception(exc, output=Output.Detailed):
    A = find_alleviation(exc)
    if not A:
        print(type(exc))
        return

    A.run(exc, output)


    #print(type(exc))
    #if isinstance(exc, os.error):
    #    print('Got OS Error: {}'.format(exc.strerror))
    #elif isinstance(exc, exceptions.IOError):
    #    print('Got IOError: {}'.format(exc.strerror))
