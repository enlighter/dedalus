

import numpy as np


class Field(object):

    def __init__(self, name):

        self.name = name
        self.data = 0.

    def __repr__(self):

        return self.name

    def __neg__(self):

        return Negative(self)

    def __add__(self, other):

        return Add(self, other)

    def __sub__(self, other):

        return Subtract(self, other)

    def __mul__(self, other):

        return Multiply(self, other)


class Operator(object):

    name = 'Operator'
    n_args = None

    def __init__(self, *args):

        # Inputs
        self.args = list(args)

        # Check number of arguments
        if self.n_args:
            if len(args) != self.n_args:
                raise ValueError("Wrong number of arguments.")

        # Check argument types
        for a in args:
            if not isinstance(a, (Field, Operator)):
                raise TypeError("Operators cannot accept arguments of type '%s'." %type(a).__name__)

    def __repr__(self):

        # Represent as "name(arguments)"
        r_op = self.name
        r_args = [a.__repr__() for a in self.args]

        return r_op + '(' + ', '.join(r_args) + ')'

    def evaluate_args(self):

        # Recursively evaluate operator arguments
        for i, a in enumerate(self.args):
            if isinstance(a, Operator):
                self.args[i] = a.evaluate()

    def evaluate(self):

        # This must be implemented in subclasses
        raise NotImplementedError()

    def __neg__(self):

        return Negative(self)

    def __add__(self, other):

        return Add(self, other)

    def __sub__(self, other):

        return Subtract(self, other)

    def __mul__(self, other):

        return Multiply(self, other)


class Negative(Operator):

    name = 'Neg'
    n_args = 1

    def __str__(self):

        # Print as "-arg"
        s_arg = self.args[0].__str__()

        return '-' + s_arg

    def evaluate(self):

        self.evaluate_args()
        out_name = '(' + self.__str__() + ')'
        out = Field(out_name)
        out.data = -self.args[0].data

        return out


class Add(Operator):

    name = 'Add'
    n_args = 2

    def __str__(self):

        # Print as "arg1 + arg2"
        s_args = [a.__str__() for a in self.args]

        return ' + '.join(s_args)

    def evaluate(self):

        self.evaluate_args()
        out_name = '(' + self.__str__() + ')'
        out = Field(out_name)
        out.data = self.args[0].data + self.args[1].data

        return out


class Subtract(Operator):

    name = 'Sub'
    n_args = 2

    def __str__(self):

        # Print as "arg1 - arg2"
        s_args = [a.__str__() for a in self.args]

        return ' - '.join(s_args)

    def evaluate(self):

        self.evaluate_args()
        out_name = '(' + self.__str__() + ')'
        out = Field(out_name)
        out.data = self.args[0].data - self.args[1].data

        return out


class Multiply(Operator):

    name = 'Mult'
    n_args = 2

    def __str__(self):

        # Print as "arg1 * arg2"
        s_args = [a.__str__() for a in self.args]

        # Parenthesize addition, subtraction, and negation
        for i, a in enumerate(self.args):
            if isinstance(a, (Negative, Add, Subtract)):
                s_args[i] = '(' + s_args[i] + ')'

        return ' * '.join(s_args)

    def evaluate(self):

        self.evaluate_args()
        out_name = '(' + self.__str__() + ')'
        out = Field(out_name)
        out.data = self.args[0].data * self.args[1].data

        return out

