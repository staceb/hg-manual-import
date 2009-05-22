# This is a part of Esotope Brainfuck-to-C Compiler.

import sys
import cStringIO as stringio

from bfc.nodes import *
from bfc.expr import *
from bfc.cond import *
from bfc.codegen import Generator

class CGenerator(Generator):
    def __init__(self, compiler):
        Generator.__init__(self, compiler)
        self.declbuf = stringio.StringIO()
        self.buf = stringio.StringIO()
        self.nextvars = {} 

    def newvariable(self, prefix):
        index = self.nextvars.get(prefix, 0)
        self.nextvars[prefix] = index + 1

        name = prefix + str(index)
        self.write('int %s;' % name)
        return name

    def dumpcomplex(self, node):
        stride = node.stride()
        if stride is None:
            self.write('// stride: unknown')
        elif stride != 0:
            self.write('// stride: %s' % stride)

        updates = node.postupdates()
        if updates:
            self.write('// clobbers: %r' % updates)

    def write(self, line):
        self.buf.write('\t' * self.nindents + line + '\n')

    def flush(self):
        sys.stdout.write(self.declbuf.getvalue())
        self.declbuf.reset()
        self.declbuf.truncate()

        sys.stdout.write(self.buf.getvalue())
        self.buf.reset()
        self.buf.truncate()

    ############################################################

    def generateexpr(self, expr):
        if isinstance(expr, (int, long)): return str(expr)

        stack = []
        for c in expr._simplify(expr.code):
            if c is Expr.NEG:
                arg = stack.pop()
                stack.append('-%s' % arg)
            elif c is Expr.REF:
                arg = stack.pop()
                stack.append('p[%s]' % arg)
            elif c is Expr.ADD:
                rhs = stack.pop(); lhs = stack.pop()
                if rhs.startswith('-'):
                    stack.append('(%s%s)' % (lhs, rhs))
                else:
                    stack.append('(%s+%s)' % (lhs, rhs))
            elif c is Expr.MUL:
                rhs = stack.pop(); lhs = stack.pop()
                stack.append('(%s*%s)' % (lhs, rhs))
            elif c is Expr.DIV:
                rhs = stack.pop(); lhs = stack.pop()
                stack.append('(%s/%s)' % (lhs, rhs))
            elif c is Expr.MOD:
                rhs = stack.pop(); lhs = stack.pop()
                stack.append('(%s%%%s)' % (lhs, rhs))
            else:
                stack.append(str(c))
        return stack[-1]

    def generatecond(self, cond):
        if isinstance(cond, Always):
            return '1'
        elif isinstance(cond, Never):
            return '0'
        elif isinstance(cond, MemNotEqual):
            if cond.value == 0:
                return 'p[%d]' % cond.target
            else:
                return 'p[%d] != %d' % (cond.target, cond.value)
        elif isinstance(cond, ExprNotEqual):
            return '%s != %d' % (self.generateexpr(cond.expr), cond.value)
        else:
            assert False

    ############################################################

    def _formatadjust(self, ref, value):
        if isinstance(value, (int, long)) or value.simple():
            value = int(value)
            if value == 0:
                return ''
            elif value == 1:
                return '++%s' % ref
            elif value == -1:
                return '--%s' % ref

        s = self.generateexpr(value)
        if s.startswith('-'):
            return '%s -= %s' % (ref, s[1:])
        else:
            return '%s += %s' % (ref, s)

    _reprmap = [('\\%03o', '%c')[32 <= i < 127] % i for i in xrange(256)]
    _reprmap[0] = '\\0'; _reprmap[9] = '\\t'; _reprmap[10] = '\\n'; _reprmap[13] = '\\r'
    _reprmap[34] = '\\"'; _reprmap[39] = '\''; _reprmap[92] = '\\\\'
    def _addslashes(self, s, _reprmap=_reprmap):
        return ''.join(_reprmap[ord(i)] for i in s)

    def generate_Program(self, node):
        self.getcused = self.putcused = self.putsused = False
        self.write('static uint%d_t m[30000], *p = m;' % self.cellsize)
        self.write('int main(void) {')
        self.indent()

        returns = True
        genmap = self.genmap
        for child in node:
            genmap[type(child)](child)
            returns &= child.returns()

        if returns:
            self.write('return 0;')
        self.dedent()
        self.write('}')

        # build declarations
        self.declbuf.write('/* generated by esotope-bfc */\n')
        self.declbuf.write('#include <stdio.h>\n')
        self.declbuf.write('#include <stdint.h>\n')
        if self.getcused:
            self.declbuf.write('#define GETC() (fflush(stdout), fgetc(stdin))\n')
        if self.putcused:
            self.declbuf.write('#define PUTC(c) fputc(c, stdout)\n')
        if self.putsused:
            self.declbuf.write('#define PUTS(s) fwrite(s, 1, sizeof(s)-1, stdout)\n')

    def generate_Nop(self, node):
        pass # do nothing

    def generate_SetMemory(self, node):
        self.write('p[%d] = %s;' % (node.offset, self.generateexpr(node.value)))

    def generate_AdjustMemory(self, node):
        stmt = self._formatadjust('p[%d]' % node.offset, node.delta)
        if stmt: self.write(stmt + ';')

    def generate_MovePointer(self, node):
        stmt = self._formatadjust('p', node.offset)
        if stmt: self.write(stmt + ';')

    def generate_Input(self, node):
        self.getcused = True
        self.write('p[%d] = GETC();' % node.offset)

    def generate_Output(self, node):
        self.putcused = True
        self.write('PUTC(%s);' % self.generateexpr(node.expr))

    def generate_OutputConst(self, node):
        self.putsused = True
        for line in node.str.splitlines(True):
            self.write('PUTS("%s");' % self._addslashes(line))

    def generate_SeekMemory(self, node):
        self.write('while (p[%d] != %d) %s;' % (node.target, node.value,
                self._formatadjust('p', node.stride)))

    def generate_If(self, node):
        if self.debugging:
            self.dumpcomplex(self)

        self.write('if (%s) {' % self.generatecond(node.cond))
        self._generatenested(node)
        self.write('}')

    def generate_Repeat(self, node):
        if node.count.code[-1] == '@': # TODO more generic code
            count = node.count # since the memory cell is already within the range.
        else:
            count = node.count % (1 << self.cellsize)

        if self.debugging:
            self.dumpcomplex(self)

        var = self.newvariable('loopcnt')
        self.write('for (%s = %s; %s > 0; --%s) {' %
                (var, self.generateexpr(count), var, var))
        self._generatenested(node)
        self.write('}')

    def generate_While(self, node):
        if self.debugging:
            self.dumpcomplex(self)

        if isinstance(node.cond, Always) and len(node) == 0:
            self.write('while (1); /* infinite loop */')
        else:
            self.write('while (%s) {' % self.generatecond(node.cond))
            self._generatenested(node)
            self.write('}')

