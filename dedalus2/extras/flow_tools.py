"""
Extra tools that are useful in hydrodynamical problems.

"""

import numpy as np
from mpi4py import MPI

from ..data import operators
from ..data.field import Array
from ..data.future import FutureField

import logging
logger = logging.getLogger(__name__.split('.')[-1])


class GlobalArrayReducer:
    """
    Directs parallelized reduction of distributed array data.

    Parameters
    ----------
    comm : MPI communicator
        MPI communicator
    dtype : data type, optional
        Array data type (default: np.float64)

    """

    def __init__(self, comm, dtype=np.float64):

        self.comm = comm
        self._scalar_buffer = np.zeros(1, dtype=dtype)

    def reduce_scalar(self, local_scalar, mpi_reduce_op):
        """Compute global reduction of a scalar from each process."""
        self._scalar_buffer[0] = local_scalar
        self.comm.Allreduce(MPI.IN_PLACE, self._scalar_buffer, op=mpi_reduce_op)
        return self._scalar_buffer[0]

    def global_min(self, data, empty=np.inf):
        """Compute global min of all array data."""
        if data.size:
            local_min = np.min(data)
        else:
            local_min = empty
        return self.reduce_scalar(local_min, MPI.MIN)

    def global_max(self, data, empty=-np.inf):
        """Compute global max of all array data."""
        if data.size:
            local_max = np.max(data)
        else:
            local_max = empty
        return self.reduce_scalar(local_max, MPI.MAX)

    def global_mean(self, data):
        """Compute global mean of all array data."""
        local_sum = np.sum(data)
        local_size = data.size
        global_sum = self.reduce_scalar(local_sum, MPI.SUM)
        global_size = self.reduce_scalar(local_size, MPI.SUM)
        return global_sum / global_size


class GlobalFlowProperty:
    """
    Directs parallelized determination of a global flow property on the grid.

    Parameters
    ----------
    solver : solver object
        Problem solver
    cadence : int, optional
        Iteration cadence for property evaluation (default: 1)

    Examples
    --------
    >>> flow = GlobalFlowProperty(solver)
    >>> flow.add_property('sqrt(u*u + w*w) * Lz / nu', name='Re')
    ...
    >>> flow.max('Re')
    1024.5

    """

    def __init__(self, solver, cadence=1):

        self.solver = solver
        self.cadence = cadence
        self.reducer = GlobalArrayReducer(solver.domain.dist.comm_cart)
        self.properties = solver.evaluator.add_dictionary_handler(iter=cadence)

    def add_property(self, property, name):
        """Add a property."""
        self.properties.add_task(property, layout='g', name=name)

    def min(self, name):
        """Compute global min of a property on the grid."""
        gdata = self.properties[name]['g']
        return self.reducer.global_min(gdata)

    def max(self, name):
        """Compute global max of a property on the grid."""
        gdata = self.properties[name]['g']
        return self.reducer.global_max(gdata)

    def grid_average(self, name):
        """Compute global mean of a property on the grid."""
        gdata = self.properties[name]['g']
        return self.reducer.global_mean(gdata)

    def volume_average(self, name):
        """Compute volume average of a property."""
        # Compute volume integral
        field = self.properties[name]
        integral_op = operators.Integrate(field)
        integral_field = integral_op.operate()
        # Communicate integral value to all processes
        integral_value = self.reducer.global_max(integral_field['g'])
        average_value = integral_value / self.domain.hypervolume
        return average_value


class CFL:
    """
    Computes CFL-limited timestep from a set of frequencies/velocities.

    Parameters
    ----------
    solver : solver object
        Problem solver
    initial_dt : float
        Initial timestep
    cadence : int, optional
        Iteration cadence for computing new timestep (default: 1)
    safety : float, optional
        Safety factor for scaling computed timestep (default: 1.)
    max_dt : float, optional
        Maximum allowable timestep (default: inf)
    min_dt : float, optional
        Minimum allowable timestep (default: 0.)
    max_change : float, optional
        Maximum fractional change between timesteps (default: inf)
    min_change : float, optional
        Minimum fractional change between timesteps (default: 0.)

    Notes
    -----
    The new timestep is computed by summing across the provided frequencies
    for each grid point, and then reciprocating the maximum "total" frequency
    from the entire grid.

    """

    def __init__(self, solver, initial_dt, cadence=1, safety=1., max_dt=np.inf,
                 min_dt=0., max_change=np.inf, min_change=0.):

        self.solver = solver
        self.stored_dt = initial_dt
        self.cadence = cadence
        self.safety = safety
        self.max_dt = max_dt
        self.min_dt = min_dt
        self.max_change = max_change
        self.min_change = min_change

        domain = solver.domain
        self.grid_spacings = []
        for axis in range(domain.dim):
            dx_array = Array(domain)
            dx_array.from_local_vector(domain.grid_spacing(axis, domain.dealias), axis)
            self.grid_spacings.append(dx_array)
        self.reducer = GlobalArrayReducer(solver.domain.dist.comm_cart)
        self.frequencies = solver.evaluator.add_dictionary_handler(iter=cadence)

    def compute_dt(self):
        """Compute CFL-limited timestep."""
        iteration = self.solver.iteration
        # Return initial dt on first iteration
        if iteration == 0:
            return self.stored_dt
        # Otherwise compute new timestep when cadence divides previous iteration
        # (this is when the frequency dicthandler is freshly updated)
        if (iteration-1) % self.cadence == 0:
            # Sum across frequencies for each local grid point
            local_freqs = np.sum(np.abs(field['g']) for field in self.frequencies.fields.values())
            # Compute new timestep from max frequency across all grid points
            max_global_freq = self.reducer.global_max(local_freqs)
            if max_global_freq == 0.:
                dt = np.inf
            else:
                dt = 1 / max_global_freq
            # Apply restrictions
            dt *= self.safety
            dt = min(dt, self.max_dt, self.max_change*self.stored_dt)
            dt = max(dt, self.min_dt, self.min_change*self.stored_dt)
            self.stored_dt = dt
        return self.stored_dt

    def add_frequency(self, freq):
        """Add an on-grid frequency."""
        self.frequencies.add_task(freq, layout='g')

    def add_velocity(self, velocity, axis):
        """Add grid-crossing frequency from a velocity along one axis."""
        vel = FutureField.parse(velocity, self.solver.evaluator.vars, self.solver.domain)
        freq = vel / self.grid_spacings[axis]
        self.add_frequency(freq)

    def add_velocities(self, components):
        """Add grid-crossing frequencies from a tuple of velocity components."""
        if len(components) != self.solver.domain.dim:
            raise ValueError("Wrong number of components for domain.")
        for axis, component in enumerate(components):
            self.add_velocity(component, axis)

