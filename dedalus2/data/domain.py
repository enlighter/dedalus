"""
Problem domains.

"""

import numpy as np

from .distributor import Distributor
from .field import Field
from .pencil import Pencil
from ..tools.cache import CachedMethod
from ..tools.array import reshape_vector


class Domain:
    """
    Problem domain composed of orthogonal bases.

    Parameters
    ----------
    bases : list of basis objects
        Bases composing the domain
    grid_dtype : dtype
        Grid data type
    mesh : tuple of ints, optional
        Process mesh for parallelization (default: 1-D mesh of available processes)

    Attributes
    ----------
    dim : int
        Dimension of domain, equal to length of bases list
    distributor : distributor object
        Data distribution controller

    """

    def __init__(self, bases, grid_dtype=np.complex128, mesh=None):

        # Initial attributes
        self.bases = bases
        self.dim = len(bases)
        self.grid_dtype = grid_dtype

        # Iteratively set basis data types
        # (Grid-to-coefficient transforms proceed in the listed order)
        for b in self.bases:
            grid_dtype = b.set_transforms(grid_dtype)

        # Manage field allocation
        self._field_cache = list()
        self._field_count = 0

        # Create distributor
        self.distributor = Distributor(self, mesh)

    @CachedMethod
    def grid(self, axis):
        """Return local grid along specified axis."""

        # Get local part of basis grid
        start = self.distributor.grid_layout.start[axis]
        size = self.distributor.grid_layout.shape[axis]
        grid = self.bases[axis].grid[start:start+size]

        # Reshape as multidimensional vector
        grid = reshape_vector(grid, self.dim, axis)

        return grid

    def _collect_field(self, field):
        """Cache free field."""

        # Clean field
        field.layout = self.distributor.coeff_layout
        field.data.fill(0)

        # Add to cache
        self._field_cache.append(field)

    def new_field(self):
        """Return a free field."""

        # Return a previously allocated field, if available
        if self._field_cache:
            return self._field_cache.pop()
        # Otherwise instantiate a new field
        else:
            return Field(self)

