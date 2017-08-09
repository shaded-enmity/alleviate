import collections
import dis
import opcode

"""
op    - the instruction number like dis.CALL_FUNCTION
stack - how many elements we should pop off the stack
oparg - the original decodable oparg of the instruction
imm   - immediate value of the instruction
"""
Opcode = collections.namedtuple('Opcode', 'op stack oparg imm')


def _oparg_to_args_kwargs(oparg):
    ''' Extract `oparg` components as tuple '''
    # TODO: figure out how to handle EXTENDED_ARG: ((oparg >> 16) & 255) ?
    return (oparg & 255, ((oparg >> 8) & 255) * 2)


def disassemble(co, lasti):
    ''' Disassemble code in `co` and find top of the stack (TOS)
        by looking at the last instruction number in `lasti` 
        and return List[Opcode] and index into that list which
        represent the TOS.
    '''
    code = co.co_code
    n = len(code)
    i = 0
    extended_arg = 0
    free = None
    tos = 0
    instructions = []

    while i < n:
        c = code[i]
        op = ord(c)

        if i == lasti: 
            tos = len(instructions)

        imm = None
        stack = 0
        i = i+1

        if op >= dis.HAVE_ARGUMENT:
            oparg = ord(code[i]) + ord(code[i+1])*256 + extended_arg
            extended_arg = 0
            i = i+2
            if op == dis.EXTENDED_ARG:
                extended_arg = oparg*65536L
            stack = (oparg & 255) + ((oparg >> 8) & 255) * 2
            if op in dis.hasconst:
                imm = co.co_consts[oparg]
            elif op in dis.hasname:
                imm = co.co_names[oparg]
            elif op in dis.hasjrel:
                imm = i + oparg
            elif op in dis.haslocal:
                imm = co.co_varnames[oparg]
            elif op in dis.hascompare:
                imm = dis.cmp_op[oparg]
            elif op in dis.hasfree:
                if free is None:
                    free = co.co_cellvars + co.co_freevars
                imm = free[oparg]

        instructions.append(Opcode(op, stack, oparg, imm))

    return tos, instructions


def _consume_until_load_global(insns, start):
    ''' Iterate the instruction stream `insns` from `start`
        and collect all LOAD_ATTR opcodes until we hit LOAD_GLOBAL
    '''
    stack = []
    for insn in insns[start::-1]:
        if insn.op == opcode.opmap['LOAD_ATTR']:
            stack.append(insn.imm)
        if insn.op == opcode.opmap['LOAD_GLOBAL']:
            stack.append(insn.imm)
            break
    return tuple(stack[::-1])


def _disas_call(code, lasti):
    ''' Check that the TOS instruction is CALL_FUNCTION, if it is, return the 
        name of the function and all relevant instructions leading to that point

    '''
    tos, instructions = disassemble(code, lasti)
    tos_insn = instructions[tos]
    if tos_insn.op != opcode.opmap['CALL_FUNCTION']:
        return None, None
    start = tos - instructions[tos].stack - 1
    function_name = _consume_until_load_global(instructions, start)
    return list(reversed(instructions[tos - instructions[tos].stack - 1:tos])), function_name


def _handle_opcode(opc, frame):
    ''' Handle opcode `opc` in the context of `frame` in order to resolve
        the `imm` immediate value of the opcode

    '''
    if opc.op == opcode.opmap['LOAD_GLOBAL']:
        if opc.imm not in frame.f_globals:
            if '__builtins__' in frame.f_globals and opc.imm in dir(frame.f_globals['__builtins__']):
                # this references a built-in, eval() it to get refrence to the object
                return eval(opc.imm)
            else:
                return None
        else:
            return frame.f_globals[opc.imm]
    else:
        return opc.imm


def _get_function_from_name(name, frame):
    ''' For function call like 'os.open()' we receive a tuple ('os', 'open')

        IF the tuple has a single element, it's either something global/local or a builtin
    '''
    G = frame.f_globals.copy()
    G.update(frame.f_locals)
    builtins = G['__builtins__'] if '__builtins__' in G else None
    if len(name) == 1:
        if builtins and name[0] in dir(builtins):
            # a better way to get a hold of built-in ?
            return eval(name[0])
        else:
            return G[name[0]]
    else:
        # This is tricky, G is a dict, but subsequent
        # members are modules, so we need to use getattr
        # after the first element
        cG = G[name[0]]
        fn = name[-1]
        for n in name[1:-1]:
            cG = getattr(G, n)
        return getattr(cG, fn)


def _pairwise(it):
    ''' Simple pairwise iterator  '''
    it = iter(it)
    while True:
        yield next(it), next(it)


def get_function_and_args(code, lasti, frame):
    ''' Get called function name from `code` in the context of `frame` and 
        and both args & kwargs at the point of `lasti` of the call

    '''
    instructions, name = _disas_call(code, lasti)
    if not instructions:
        return

    call = instructions[0]
    n_args, n_kwargs = _oparg_to_args_kwargs(call.oparg)
    off_kwargs = n_kwargs
    off_args = off_kwargs + n_args
    kwargs = []

    for v, k in _pairwise(instructions[1:off_kwargs]):
        key = _handle_opcode(k, frame)
        value = _handle_opcode(v, frame)
        kwargs.append((key, value))

    args = [_handle_opcode(x, frame) 
            for x in instructions[off_kwargs:off_args]]

    return ('.'.join(name), _get_function_from_name(name, frame), 
            args[::-1], dict(kwargs))

