

import numpy as np
import weakref


# Bottom of module:
# # Import after definitions to resolve cyclic dependencies
# from .operators import Negation, Addition, Subtraction, Multiplication


class Field:
    """Scalar field defined over the distributed domain."""

    def __init__(self, domain, name=None):

        # Initial attributes
        if name is not None:
            self.name = name
        else:
            self.name = 'F' + str(id(self))

        # Weak reference to domain to allow cyclic garbage collection
        self.domain = weakref.ref(domain)

        # Increment domain field count
        domain._field_count += 1

        # Allocate buffer
        self._buffer = np.zeros(domain.distributor.buffer_size, dtype=np.byte)

        # Set initial layout
        self.layout = domain.distributor.grid_layout

    @property
    def layout(self):
        return self._layout

    @layout.setter
    def layout(self, layout):
        self._layout = layout
        self.data = layout.view_data(self._buffer)

    def __del__(self):

        # Add self to domain manager
        if self.domain():
            self.domain()._collect_field(self)

    def __repr__(self):
        return self.name

    def __neg__(self):
        return Negation(self)

    def __add__(self, other):
        return Addition(self, other)

    def __radd__(self, other):
        return Addition(other, self)

    def __sub__(self, other):
        return Subtraction(self, other)

    def __rsub__(self, other):
        return Subtraction(other, self)

    def __mul__(self, other):
        return Multiplication(self, other)

    def __rmul__(self, other):
        return Multiplication(other, self)

    def __getitem__(self, layout):

        if isinstance(layout, str):
            layout = self.domain.distributor.string_references[layout]

        if self.layout.index < layout.index:
            while self.layout.index < layout.index:
                self._towards_grid_space()
        elif self.layout.index > layout.index:
            while self.layout.index > layout.index:
                self._towards_coeff_space()

        return self.data:

    def __setitem__(self, layout, data):

        if isinstance(layout, str):
            layout = self.domain.distributor.string_references[layout]

        self.layout = layout
        np.copyto(data, self.data)

    def _towards_grid_space(self):
        """Change to next layout towards grid space."""

        pass

    def _towards_coeff_space(self):
        """Change to next layout towards coefficient space."""

        pass

    def require_grid_space(self, axis=None):
        """Require one axis (default: all axes) to be in grid space."""

        if axis is None:
            while not all(self.layout.grid_space)
                self._towards_grid_space()
        else:
            while not self.layout.grid_space[axis]:
                self._towards_grid_space()

    def require_coeff_space(self, axis=None):
        """Require one axis (default: all axes) to be in coefficient space."""

        if axis is None:
            while any(self.layout.grid_space):
                self._towards_coeff_space()
        else:
            while self.layout.grid_space[axis]:
                self._towards_coeff_space()

    def require_local(self, axis):
        """Require an axis to be local."""

        # Handle negative axes
        if axis < 0:
            axis += self.domain.dim

        while not self.layout.local[axis]:
            if axis == 0:
                self._towards_grid_space()
            elif axis == 1:
                self._towards_coeff_space()
            else:
                raise ValueError("Assumption that axis > 1 always local has failed.")

    def differentiate(self, axis, out):
        """Differentiate field across one axis."""

        # Require axis to be local and in coefficient space
        self.require_local(axis)
        self.require_coeff_space(axis)

        # Call basis differentiation
        out.layout = self.layout
        self.domain().bases[axis].differentiate(self.data, out.data, axis=axis)

    def integrate(self, axes=None):
        """Integrate field over domain."""

        # Integrate over all axes by default
        if axes is None:
            axes = range(self.domain.dim)
        else:
            axes = list(axis)

        # Integrate by coefficients
        data = field['K']
        for i in reversed(sorted(axes)):
            b = self.domain.bases[i]
            data = b.integrate(data, i)
            data = b.grid_dtype(data)

        return data


# Import after definitions to resolve cyclic dependencies
from .operators import Negation, Addition, Subtraction, Multiplication

