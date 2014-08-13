"""
Abstract and built-in classes for spectral bases.

"""

import logging
import math
import numpy as np
from numpy import pi
from scipy import sparse
from scipy import fftpack

from ..data import operators
from ..libraries.fftw import fftw_wrappers as fftw
from ..tools.config import config
from ..tools.cache import CachedAttribute
from ..tools.cache import CachedMethod
from ..tools.array import interleaved_view
from ..tools.array import reshape_vector
from ..tools.array import axslice


logger = logging.getLogger(__name__.split('.')[-1])
DEFAULT_LIBRARY = config['transforms'].get('DEFAULT_LIBRARY')
FFTW_RIGOR = config['transforms'].get('FFTW_RIGOR')


class Basis:
    """
    Base class for spectral bases.

    These classes define methods for transforming, differentiating, and
    integrating corresponding series represented by their spectral coefficients.

    Parameters
    ----------
    base_grid_size : int
        Number of grid points
    interval : tuple of floats
        Spatial domain of basis
    dealias : float, optional
        Fraction of modes to keep after dealiasing (default: 1.)

    Attributes
    ----------
    grid_dtype : dtype
        Grid data type
    coeff_size : int
        Number of spectral coefficients
    coeff_embed : int
        Padded number of spectral coefficients for transform
    coeff_dtype : dtype
        Coefficient data type

    """

    def __repr__(self):
        return '<%s %i>' %(self.__class__.__name__, id(self))

    def __str__(self):
        if self.name:
            return self.name
        else:
            return self.__repr__()

    def set_dtype(self, grid_dtype):
        """Set transforms based on grid data type."""

        raise NotImplementedError()
        return coeff_dtype

    def forward(self, gdata, *, axis, cdata=None):
        """Grid-to-coefficient transform."""

        raise NotImplementedError()
        return cdata

    def backward(self, cdata, *, axis, gdata=None, scale=1.):
        """Coefficient-to-grid transform."""

        raise NotImplementedError()
        return gdata

    def differentiate(self, cdata, cderiv, axis):
        """Differentiate using coefficients."""

        raise NotImplementedError()

    def integrate(self, cdata, cint, axis):
        """Integrate over interval using coefficients."""

        raise NotImplementedError()

    def interpolate(self, cdata, cint, position, axis):
        """Interpolate in interval using coefficients."""

        raise NotImplementedError()

    @property
    def library(self):
        return self._library

    @library.setter
    def library(self, value):
        self.forward = getattr(self, '_forward_%s' %value.lower())
        self.backward = getattr(self, '_backward_%s' %value.lower())
        self._library = value.lower()

    def grid_size(self, scale):
        """Compute scaled grid size."""

        grid_size = float(scale) * self.base_grid_size
        if not grid_size.is_integer():
            raise ValueError("Scaled grid size is not an integer: %f" %grid_size)
        return int(grid_size)

    def check_arrays(self, cdata, gdata, axis, scale=None):
        """
        Verify provided arrays sizes and dtypes are correct.
        Build compliant arrays if not provided.

        """

        if cdata is None:
            # Build cdata
            cshape = list(gdata.shape)
            cshape[axis] = self.coeff_size
            cdata = fftw.create_array(cshape, self.coeff_dtype)
        else:
            # Check cdata
            if cdata.shape[axis] != self.coeff_size:
                raise ValueError("cdata does not match coeff_size")
            if cdata.dtype != self.coeff_dtype:
                raise ValueError("cdata does not match coeff_dtype")

        if scale:
            grid_size = self.grid_size(scale)

        if gdata is None:
            # Build gdata
            gshape = list(cdata.shape)
            gshape[axis] = grid_size
            gdata = fftw.create_array(gshape, self.grid_dtype)
        else:
            # Check gdata
            if scale and (gdata.shape[axis] != grid_size):
                raise ValueError("gdata does not match scaled grid_size")
            if gdata.dtype != self.grid_dtype:
                raise ValueError("gdata does not match grid_dtype")

        return cdata, gdata


class TransverseBasis(Basis):
    """Base class for bases supporting transverse differentiation."""

    def trans_diff(self, i):
        """Transverse differentation constant for i-th term."""

        raise NotImplementedError()


class ImplicitBasis(Basis):
    """
    Base class for bases supporting implicit methods.

    These bases define the following matrices encoding the respective linear
    functions acting on a series represented by its spectral coefficients:

    Linear operators (square matrices):
        Pre     : preconditioning (default: identity)
        Diff    : differentiation
        Mult(p) : multiplication by p-th basis element

    Linear functionals (vectors):
        left_vector   : left-endpoint evaluation
        right_vector  : right-endpoint evaluation
        integ_vector  : integration over interval
        interp_vector : interpolation in interval

    Additionally, they define a column vector `bc_vector` indicating which
    coefficient's Galerkin constraint is to be replaced by the boundary
    condition on a differential equation (i.e. the order of the tau term).

    """

    @CachedAttribute
    def Pre(self):
        """Preconditioning matrix."""

        # Default to identity matrix
        Pre = sparse.identity(self.coeff_size, dtype=self.coeff_dtype)

        return Pre.tocsr()

    @CachedMethod
    def Mult(self, p, subindex):
        """p-element multiplication matrix."""

        raise NotImplementedError()

    @CachedAttribute
    def bc_vector(self):
        """Boundary-row column vector."""

        raise NotImplementedError()


class Chebyshev(ImplicitBasis):
    """Chebyshev polynomial basis on the roots grid."""

    element_label = 'T'
    tau_row = -1

    def __init__(self, base_grid_size, interval=(-1,1), dealias=1, name=None):

        self.subbases = [self]

        # Coordinate transformation
        # Native interval: (-1, 1)
        radius = (interval[1] - interval[0]) / 2
        center = (interval[1] + interval[0]) / 2
        self._grid_stretch = radius / 1
        self._native_coord = lambda xp: (xp - center) / radius
        self._problem_coord = lambda xn: center + (xn * radius)

        # Attributes
        self.base_grid_size = base_grid_size
        self.interval = tuple(interval)
        self.dealias = dealias
        self.name = name
        self.library = DEFAULT_LIBRARY

    def default_meta(self):
        return {'constant': False,
                'scale': None}

    @CachedMethod
    def grid(self, scale=1.):
        """Build Chebyshev roots grid."""

        grid_size = self.grid_size(scale)
        i = np.arange(grid_size)
        theta = pi * (i + 1/2) / grid_size
        native_grid = -np.cos(theta)
        return self._problem_coord(native_grid)

    def set_dtype(self, grid_dtype):
        """Determine coefficient properties from grid dtype."""

        # Transform retains data type
        self.grid_dtype = np.dtype(grid_dtype)
        self.coeff_dtype = self.grid_dtype
        # Same number of modes and grid points
        self.coeff_size = self.base_grid_size
        self.elements = np.arange(self.coeff_size)

        return self.coeff_dtype

    @staticmethod
    def _resize_coeffs(cdata_in, cdata_out, axis):
        """Resize coefficient data by padding/truncation."""

        size_in = cdata_in.shape[axis]
        size_out = cdata_out.shape[axis]

        if size_in < size_out:
            # Pad with higher order polynomials at end of data
            np.copyto(cdata_out[axslice(axis, 0, size_in)], cdata_in)
            np.copyto(cdata_out[axslice(axis, size_in, None)], 0)
        elif size_in > size_out:
            # Truncate higher order polynomials at end of data
            np.copyto(cdata_out, cdata_in[axslice(axis, 0, size_out)])
        else:
            np.copyto(cdata_out, cdata_in)

    @staticmethod
    def _forward_scaling(pdata, axis):
        """Scale DCT output to Chebyshev coefficients."""

        # Scale as Chebyshev amplitudes
        pdata *= 1 / pdata.shape[axis]
        pdata[axslice(axis, 0, 1)] *= 0.5
        # Negate odd modes for natural grid ordering
        pdata[axslice(axis, 1, None, 2)] *= -1.

    @staticmethod
    def _backward_scaling(pdata, axis):
        """Scale Chebyshev coefficients to IDCT input."""

        # Negate odd modes for natural grid ordering
        pdata[axslice(axis, 1, None, 2)] *= -1.
        # Scale from Chebyshev amplitudes
        pdata[axslice(axis, 1, None)] *= 0.5

    def _forward_scipy(self, gdata, *, axis, cdata=None):
        """Forward transform using scipy DCT."""

        cdata, gdata = self.check_arrays(cdata, gdata, axis)
        # View complex data as interleaved real data
        if gdata.dtype == np.complex128:
            gdata = interleaved_view(gdata)
            cdata = interleaved_view(cdata)
        # Scipy DCT
        temp = fftpack.dct(gdata, type=2, axis=axis)
        # Scale DCT output to Chebyshev coefficients
        self._forward_scaling(temp, axis)
        # Pad / truncate coefficients
        self._resize_coeffs(temp, cdata, axis)

        return cdata

    def _backward_scipy(self, cdata, *, axis, gdata=None, scale=1.):
        """Backward transform using scipy IDCT."""

        cdata, gdata = self.check_arrays(cdata, gdata, axis, scale)
        # Pad / truncate coefficients
        # Store in gdata for memory efficiency (transform preserves shape/dtype)
        self._resize_coeffs(cdata, gdata, axis)
        # Scale Chebyshev coefficients to IDCT input
        self._backward_scaling(gdata, axis)
        # View complex data as interleaved real data
        if gdata.dtype == np.complex128:
            gdata = interleaved_view(gdata)
        # Scipy IDCT
        temp = fftpack.dct(gdata, type=3, axis=axis)
        np.copyto(gdata, temp)

        return gdata

    @CachedMethod
    def _fftw_setup(self, dtype, gshape, axis):
        """Build FFTW plans and temporary arrays."""
        # Note: regular method used to cache through basis instance

        logger.debug("Building FFTW DCT plan for (dtype, gshape, axis) = (%s, %s, %s)" %(dtype, gshape, axis))
        flags = ['FFTW_'+FFTW_RIGOR.upper()]
        plan = fftw.DiscreteCosineTransform(dtype, gshape, axis, flags=flags)
        temp = fftw.create_array(gshape, dtype)

        return plan, temp

    def _forward_fftw(self, gdata, *, axis, cdata=None):
        """Forward transform using FFTW DCT."""

        cdata, gdata = self.check_arrays(cdata, gdata, axis)
        plan, temp = self._fftw_setup(gdata.dtype, gdata.shape, axis)
        # Execute FFTW plan
        plan.forward(gdata, temp)
        # Scale DCT output to Chebyshev coefficients
        self._forward_scaling(temp, axis)
        # Pad / truncate coefficients
        self._resize_coeffs(temp, cdata, axis)

        return cdata

    def _backward_fftw(self, cdata, *, axis, gdata=None, scale=1.):
        """Backward transform using FFTW IDCT."""

        cdata, gdata = self.check_arrays(cdata, gdata, axis, scale)
        plan, temp = self._fftw_setup(gdata.dtype, gdata.shape, axis)
        # Pad / truncate coefficients
        self._resize_coeffs(cdata, temp, axis)
        # Scale Chebyshev coefficients to IDCT input
        self._backward_scaling(temp, axis)
        # Execute FFTW plan
        plan.backward(temp, gdata)

        return gdata

    @CachedAttribute
    def Integrate(self):
        """Build integration class."""

        class IntegrateChebyshev(operators.Integrate, operators.Coupled):
            basis = self

            @classmethod
            def matrix_form(cls):
                """Chebyshev integration: int(T_n) = (1 + (-1)^n) / (1 - n^2)"""
                size = cls.basis.coeff_size
                matrix = sparse.lil_matrix((size, size), dtype=cls.basis.coeff_dtype)
                matrix[0,:] = cls._integ_vector()
                return matrix.tocsr()

            @classmethod
            def _integ_vector(cls):
                """Chebyshev integration: int(T_n) = (1 + (-1)^n) / (1 - n^2)"""
                vector = np.zeros(cls.basis.coeff_size, dtype=cls.basis.coeff_dtype)
                for n in range(0, cls.basis.coeff_size, 2):
                    vector[n] = 2. / (1. - n*n)
                vector *= cls.basis._grid_stretch
                return vector

        return IntegrateChebyshev

    @CachedAttribute
    def Interpolate(self):
        """Buld interpolation class."""

        class InterpolateChebyshev(operators.Interpolate, operators.Coupled):
            basis = self

            def matrix_form(self):
                """Chebyshev interpolation: Tn(xn) = cos(n * acos(xn))"""
                size = self.basis.coeff_size
                matrix = sparse.lil_matrix((size, size), dtype=self.basis.coeff_dtype)
                matrix[0,:] = self._interp_vector()
                return matrix.tocsr()

            def _interp_vector(self):
                """Chebyshev interpolation: Tn(xn) = cos(n * acos(xn))"""
                xn = self.basis._native_coord(self.args[1])
                theta = np.arccos(xn)
                return np.cos(self.elements * theta)

        return InterpolateChebyshev

    @CachedAttribute
    def Differentiate(self):
        """Build differentiation class."""

        class DifferentiateChebyshev(operators.Differentiate, operators.Coupled):
            name = 'd' + self.name
            basis = self

            @classmethod
            def matrix_form(cls):
                """Chebyshev differentiation: d_x(T_n) / n = 2 T_(n-1) + d_x(T_(n-2)) / (n-2)"""
                size = cls.basis.coeff_size
                dtype = cls.basis.coeff_dtype
                stretch = cls.basis._grid_stretch
                matrix = sparse.lil_matrix((size, size), dtype=dtype)
                for i in range(size-1):
                    for j in range(i+1, size, 2):
                        if i == 0:
                            matrix[i, j] = j / stretch
                        else:
                            matrix[i, j] = 2. * j / stretch
                return matrix.tocsr()

            def explicit_form(self, input, output, axis):
                """Differentiation by recursion on coefficients."""
                # Currently setup just for last axis
                if axis != -1:
                    if axis != (len(cdata.shape) - 1):
                        raise NotImplementedError()
                # Referencess
                a = cdata
                b = cderiv
                N = self.basis.coeff_size - 1
                # Apply recursive differentiation
                b[..., N] = 0.
                b[..., N-1] = 2. * N * a[..., N]
                for i in range(N-2, 0, -1):
                    b[..., i] = 2 * (i+1) * a[..., i+1] + b[..., i+2]
                b[..., 0] = a[..., 1] + b[..., 2] / 2.
                # Scale for interval
                cderiv /= self.basis._grid_stretch

        return DifferentiateChebyshev

    @CachedAttribute
    def Pre(self):
        """
        Preconditioning matrix.

        T_n = (U_n - U_(n-2)) / 2
        U_(-n) = -U_(n-2)

        """

        size = self.coeff_size

        # Construct sparse matrix
        Pre = sparse.lil_matrix((size, size), dtype=self.coeff_dtype)
        Pre[0, 0] = 1.
        Pre[1, 1] = 0.5
        for n in range(2, size):
            Pre[n, n] = 0.5
            Pre[n-2, n] = -0.5

        return Pre.tocsr()

    def Mult(self, p, subindex):
        """
        p-element multiplication matrix

        T_p * T_n = (T_(n+p) + T_(n-p)) / 2
        T_(-n) = T_n

        """

        size = self.coeff_size

        # Construct sparse matrix
        Mult = sparse.lil_matrix((size, size), dtype=self.coeff_dtype)
        for n in range(size):
            upper = n + p
            if upper < size:
                Mult[upper, n] += 0.5
            lower = abs(n - p)
            if lower < size:
                Mult[lower, n] += 0.5

        return Mult.tocsr()

    def build_mult(self, coeffs, order):
        matrix = 0
        for p in range(order):
            matrix += coeffs[p] * self.Mult(p, 0)
        return matrix


class Fourier(TransverseBasis, ImplicitBasis):
    """Fourier complex exponential basis."""

    element_label = 'k'
    tau_row = 0

    def __init__(self, base_grid_size, interval=(0,2*pi), dealias=1, name=None):

        self.subbases = [self]

        # Coordinate transformation
        # Native interval: (0, 2π)
        start = interval[0]
        length = interval[1] - interval[0]
        self._grid_stretch = length / (2*pi)
        self._native_coord = lambda xp: (2*pi) * (xp - start) / length
        self._problem_coord = lambda xn: start + (xn / (2*pi) * length)

        # Attributes
        self.base_grid_size = base_grid_size
        self.interval = tuple(interval)
        self.dealias = dealias
        self.name = name
        self.library = DEFAULT_LIBRARY

    def default_meta(self):
        return {'constant': False,
                'scale': None}

    @CachedMethod
    def grid(self, scale=1.):
        """Build evenly spaced Fourier grid."""

        grid_size = self.grid_size(scale)
        native_grid = np.linspace(0, 2*pi, grid_size, endpoint=False)
        return self._problem_coord(native_grid)

    def set_dtype(self, grid_dtype):
        """Determine coefficient properties from grid dtype."""

        # Tranform produces complex coefficients
        self.grid_dtype = np.dtype(grid_dtype)
        self.coeff_dtype = np.dtype(np.complex128)
        # Build native wavenumbers, discarding any Nyquist mode
        kmax = (self.base_grid_size - 1) // 2
        if self.grid_dtype == np.float64:
            native_wavenumbers = np.arange(0, kmax+1)
        elif self.grid_dtype == np.complex128:
            native_wavenumbers = np.roll(np.arange(-kmax, kmax+1), -kmax)
        # Scale native wavenumbers
        self.elements = self.wavenumbers = native_wavenumbers / self._grid_stretch
        self.coeff_size = self.elements.size

        return self.coeff_dtype

    def _resize_real_coeffs(self, cdata_in, cdata_out, axis, grid_size):
        """Resize coefficient data by padding/truncation."""

        size_in = cdata_in.shape[axis]
        size_out = cdata_out.shape[axis]

        # Find maximum wavenumber (excluding Nyquist mode for even sizes)
        kmax = min((grid_size-1)//2, size_in-1, size_out-1)
        posfreq = axslice(axis, 0, kmax+1)
        badfreq = axslice(axis, kmax+1, None)

        # Copy modes up through kmax
        # For size_in < size_out, this pads with higher wavenumbers
        # For size_in > size_out, this truncates higher wavenumbers
        # For size_in = size_out, this copies the data (dropping Nyquist)
        np.copyto(cdata_out[posfreq], cdata_in[posfreq])
        np.copyto(cdata_out[badfreq], 0)

    def _resize_complex_coeffs(self, cdata_in, cdata_out, axis, *args):
        """Resize coefficient data by padding/truncation."""

        size_in = cdata_in.shape[axis]
        size_out = cdata_out.shape[axis]

        # Find maximum wavenumber (excluding Nyquist mode for even sizes)
        kmax = (min(size_in, size_out) - 1) // 2
        posfreq = axslice(axis, 0, kmax+1)
        badfreq = axslice(axis, kmax+1, -kmax)
        negfreq = axslice(axis, -kmax, None)

        # Copy modes up through +- kmax
        # For size_in < size_out, this pads with higher wavenumbers and conjugates
        # For size_in > size_out, this truncates higher wavenumbers and conjugates
        # For size_in = size_out, this copies the data (dropping Nyquist)
        np.copyto(cdata_out[posfreq], cdata_in[posfreq])
        np.copyto(cdata_out[badfreq], 0)
        np.copyto(cdata_out[negfreq], cdata_in[negfreq])

    def _forward_scipy(self, gdata, *, axis, cdata=None):
        """Forward transform using numpy RFFT / scipy FFT."""

        cdata, gdata = self.check_arrays(cdata, gdata, axis)
        grid_size = gdata.shape[axis]
        if gdata.dtype == np.float64:
            # Numpy RFFT (scipy RFFT uses real packing)
            temp = np.fft.rfft(gdata, axis=axis)
            # Pad / truncate coefficients
            self._resize_real_coeffs(temp, cdata, axis, grid_size)
        elif gdata.dtype == np.complex128:
            # Scipy FFT (faster than numpy FFT)
            temp = fftpack.fft(gdata, axis=axis)
            # Pad / truncate coefficients
            self._resize_complex_coeffs(temp, cdata, axis)
        # Scale as Fourier amplitudes
        cdata *= 1 / grid_size

        return cdata

    def _backward_scipy(self, cdata, *, axis, gdata=None, scale=1.):
        """Backward transform using numpy IRFFT / scipy IFFT."""

        cdata, gdata = self.check_arrays(cdata, gdata, axis, scale)
        grid_size = gdata.shape[axis]
        if gdata.dtype == np.float64:
            # Pad / truncate coefficients
            shape = np.copy(gdata.shape)
            shape[axis] = grid_size//2 + 1
            temp = np.zeros(shape, dtype=np.complex128)
            self._resize_real_coeffs(cdata, temp, axis, grid_size)
            # Numpy IRFFT
            temp = np.fft.irfft(temp, n=grid_size, axis=axis)
        elif gdata.dtype == np.complex128:
            # Pad / truncate coefficients
            # Store in gdata for memory efficiency (transform preserves shape/dtype)
            self._resize_complex_coeffs(cdata, gdata, axis)
            # Scipy IFFT
            temp = fftpack.ifft(gdata, axis=axis)
        # Undo built-in scaling
        np.multiply(temp, grid_size, out=gdata)

        return gdata

    @CachedMethod
    def _fftw_setup(self, dtype, gshape, axis):
        """Build FFTW plans and temporary arrays."""
        # Note: regular method used to cache through basis instance

        logger.debug("Building FFTW FFT plan for (dtype, gshape, axis) = (%s, %s, %s)" %(dtype, gshape, axis))
        flags = ['FFTW_'+FFTW_RIGOR.upper()]
        plan = fftw.FourierTransform(dtype, gshape, axis, flags=flags)
        temp = fftw.create_array(plan.cshape, np.complex128)
        if dtype == np.float64:
            resize_coeffs = self._resize_real_coeffs
        elif dtype == np.complex128:
            resize_coeffs = self._resize_complex_coeffs

        return plan, temp, resize_coeffs

    def _forward_fftw(self, gdata, *, axis, cdata=None):
        """Forward transform using FFTW FFT."""

        cdata, gdata = self.check_arrays(cdata, gdata, axis)
        plan, temp, resize_coeffs = self._fftw_setup(gdata.dtype, gdata.shape, axis)
        # Execute FFTW plan
        plan.forward(gdata, temp)
        # Scale FFT output to mode amplitudes
        temp *= 1 / gdata.shape[axis]
        # Pad / truncate coefficients
        resize_coeffs(temp, cdata, axis, gdata.shape[axis])

        return cdata

    def _backward_fftw(self, cdata, *, axis, gdata=None, scale=1.):
        """Backward transform using FFTW IFFT."""

        cdata, gdata = self.check_arrays(cdata, gdata, axis, scale)
        plan, temp, resize_coeffs = self._fftw_setup(gdata.dtype, gdata.shape, axis)
        # Pad / truncate coefficients
        resize_coeffs(cdata, temp, axis, gdata.shape[axis])
        # Execute FFTW plan
        plan.backward(temp, gdata)

        return gdata

    @CachedAttribute
    def Integrate(self):
        """Build integration class."""

        class IntegrateFourier(operators.Integrate, operators.Separable):
            basis = self

            def scalar_form(self, index):
                """Fourier integration: int(Fn) = 2 π δ(n,0)"""
                if index == 0:
                    return 2 * π * self.basis._grid_stretch
                else:
                    return 0

            def vector_form(self):
                """Fourier integration: int(Fn) = 2 π δ(n,0)"""
                vector = np.zeros(self.basis.coeff_size, dtype=self.basis.coeff_dtype)
                vector[0] = 2 * π * self.basis._grid_stretch
                return vector

        return IntegrateFourier

    @CachedAttribute
    def Interpolate(self):
        """Build interpolation class."""

        class InterpolateFourier(operators.Interpolate, operators.Coupled):
            basis = self

            def explicit_form(self, input, output, axis):
                dim = self.domain.dim
                weights = reshape_vector(self._interp_vector(), dim=dim, axis=axis)
                if self.grid_dtype == np.float64:
                    # Halve mean-mode weight (will be added twice)
                    weights.flat[0] /= 2
                    pos_interp = np.sum(input * weights, axis=axis, keepdims=True)
                    interp = pos_interp + pos_interp.conj()
                elif self.grid_dtype == np.complex128:
                    interp = np.sum(input * weights, axis=axis, keepdims=True)
                np.copyto(output[axslice(axis, 0, 1)], interp)
                np.copyto(output[axslice(axis, 1, None)], 0)

            def matrix_form(self):
                """Fourier interpolation: Fn(x) = exp(i kn x)"""
                if self.basis.coeff_dtype == np.float64:
                    raise NotImplementedError("Interpolating an R2C Fourier series cannot be done via a matrix multiplication.")
                else:
                    size = self.basis.coeff_size
                    matrix = sparse.lil_matrix((size, size), dtype=self.basis.coeff_dtype)
                    matrix[0,:] = self._interp_vector()
                    return matrix.tocsr()

            def _interp_vector(self):
                """Fourier interpolation: Fn(x) = exp(i kn x)"""
                x = self.args[1] - self.basis.interval[0]
                return np.exp(1j * self.basis.wavenumbers * x)

        return InterpolateFourier

    @CachedAttribute
    def Differentiate(self):
        """Build differentiation class."""

        class DifferentiateFourier(operators.Differentiate, operators.Separable):
            name = 'd' + self.name
            basis = self

            def scalar_form(self, index):
                """Fourier differentiation: dx(Fn) = i kn Fn"""
                return 1j * self.basis.wavenumbers[index]

            def vector_form(self):
                """Fourier differentiation: dx(Fn) = i kn Fn"""
                return 1j * self.basis.wavenumbers

        return DifferentiateFourier

    @CachedAttribute
    def HilbertTransform(self):
        """Build Hilbert transform class."""

        class HilbertTransformFourier(operators.HilbertTransform, operators.Separable):
            name = 'H' + self.name
            basis = self

            def scalar_form(self, index):
                """Hilbert transform: Hx(Fn) = -i sgn(kn) Fn"""
                return -1j * np.sign(self.basis.wavenumbers[index])

            def vector_form(self):
                """Hilbert transform: Hx(Fn) = -i sgn(kn) Fn"""
                k = self.basis.wavenumbers[index]
                return -1j * np.sign(self.basis.wavenumbers)

        return HilbertTransformFourier




class SinCos(TransverseBasis):
    """Sin/Cos series basis."""

    element_label = 'k'

    def __init__(self, base_grid_size, interval=(0,pi), dealias=1, name=None):

        self.subbases = [self]

        # Coordinate transformation
        # Native interval: (0, π)
        start = interval[0]
        length = interval[1] - interval[0]
        self._grid_stretch = length / (pi)
        self._native_coord = lambda xp: (pi) * (xp - start) / length
        self._problem_coord = lambda xn: start + (xn / (pi) * length)

        # Attributes
        self.base_grid_size = base_grid_size
        self.interval = tuple(interval)
        self.dealias = dealias
        self.name = name
        #self.library = DEFAULT_LIBRARY
        self.library = 'scipy'

    @CachedMethod
    def grid(self, scale=1.):
        """Build evenly spaced Fourier grid."""

        N = self.grid_size(scale)
        native_grid = pi * (np.arange(N) + 1/2) / N
        return self._problem_coord(native_grid)

    def set_dtype(self, grid_dtype):
        """Determine coefficient properties from grid dtype."""

        # Tranform produces complex coefficients
        self.grid_dtype = np.dtype(grid_dtype)
        self.coeff_dtype = self.grid_dtype
        # Build native wavenumbers
        native_wavenumbers = np.arange(self.base_grid_size)
        # Scale native wavenumbers
        self.elements = self.wavenumbers = native_wavenumbers / self._grid_stretch
        self.coeff_size = self.elements.size

        return self.coeff_dtype

    @staticmethod
    def _resize_coeffs(cdata_in, cdata_out, axis):
        """Resize coefficient data by padding/truncation."""

        size_in = cdata_in.shape[axis]
        size_out = cdata_out.shape[axis]

        if size_in < size_out:
            # Pad with higher order polynomials at end of data
            np.copyto(cdata_out[axslice(axis, 0, size_in)], cdata_in)
            np.copyto(cdata_out[axslice(axis, size_in, None)], 0)
        elif size_in > size_out:
            # Truncate higher order polynomials at end of data
            np.copyto(cdata_out, cdata_in[axslice(axis, 0, size_out)])
        else:
            np.copyto(cdata_out, cdata_in)

    def _forward_scipy(self, gdata, *, axis, cdata=None):
        """Forward transform using scipy DCT."""

        cdata, gdata = self.check_arrays(cdata, gdata, axis)
        # View complex data as interleaved real data
        if gdata.dtype == np.complex128:
            gdata = interleaved_view(gdata)
            cdata = interleaved_view(cdata)
        # Scipy DCT
        temp = fftpack.dct(gdata, type=2, axis=axis)
        # Scale DCT output to Chebyshev coefficients
        self._forward_scaling(temp, axis)
        # Pad / truncate coefficients
        self._resize_coeffs(temp, cdata, axis)

        return cdata

    def _backward_scipy(self, cdata, *, axis, gdata=None, scale=1.):
        """Backward transform using scipy IDCT."""

        cdata, gdata = self.check_arrays(cdata, gdata, axis, scale)
        # Pad / truncate coefficients
        # Store in gdata for memory efficiency (transform preserves shape/dtype)
        self._resize_coeffs(cdata, gdata, axis)
        # Scale Chebyshev coefficients to IDCT input
        self._backward_scaling(gdata, axis)
        # View complex data as interleaved real data
        if gdata.dtype == np.complex128:
            gdata = interleaved_view(gdata)
        # Scipy IDCT
        temp = fftpack.dct(gdata, type=3, axis=axis)
        np.copyto(gdata, temp)

        return gdata

    @CachedMethod
    def _fftw_setup(self, dtype, gshape, axis):
        """Build FFTW plans and temporary arrays."""
        # Note: regular method used to cache through basis instance

        logger.debug("Building FFTW FFT plan for (dtype, gshape, axis) = (%s, %s, %s)" %(dtype, gshape, axis))
        flags = ['FFTW_'+FFTW_RIGOR.upper()]
        plan = fftw.FourierTransform(dtype, gshape, axis, flags=flags)
        temp = fftw.create_array(plan.cshape, np.complex128)
        if dtype == np.float64:
            resize_coeffs = self._resize_real_coeffs
        elif dtype == np.complex128:
            resize_coeffs = self._resize_complex_coeffs

        return plan, temp, resize_coeffs

    def _forward_fftw(self, gdata, *, axis, cdata=None):
        """Forward transform using FFTW FFT."""

        cdata, gdata = self.check_arrays(cdata, gdata, axis)
        plan, temp, resize_coeffs = self._fftw_setup(gdata.dtype, gdata.shape, axis)
        # Execute FFTW plan
        plan.forward(gdata, temp)
        # Scale FFT output to mode amplitudes
        temp *= 1 / gdata.shape[axis]
        # Pad / truncate coefficients
        resize_coeffs(temp, cdata, axis, gdata.shape[axis])

        return cdata

    def _backward_fftw(self, cdata, *, axis, gdata=None, scale=1.):
        """Backward transform using FFTW IFFT."""

        cdata, gdata = self.check_arrays(cdata, gdata, axis, scale)
        plan, temp, resize_coeffs = self._fftw_setup(gdata.dtype, gdata.shape, axis)
        # Pad / truncate coefficients
        resize_coeffs(cdata, temp, axis, gdata.shape[axis])
        # Execute FFTW plan
        plan.backward(temp, gdata)

        return gdata

    def differentiate(self, cdata, cderiv, axis):
        """Differentiation by wavenumber multiplication."""

        dim = len(cdata.shape)
        ik = 1j * reshape_vector(self.wavenumbers, dim=dim, axis=axis)
        np.multiply(cdata, ik, out=cderiv)

    def differentiate(self, cdata, cderiv, axis, meta):

        outmeta = inmeta.copy()
        if inmeta['parity'] == 1:
            np.multiply(cdata[1:], -k[1:], out=cderiv)
            out
            out_parity = -1
        else:
            cderiv[axslice(axis, 0, 1)] = 0
            nonconst = axslice(axis, 1, None)
            np.multiply(cdata, k[nonconst], out=cderiv[nonconst])
            out_parity = 1

        outmeta = meta.copy()
        outmeta['parity'] = -1 * meta['parity']
        return outmeta


    @CachedAttribute
    def integ_vector(self):
        """
        Integral row vector.

        int(F_n) = 2 pi    if n = 0
                 = 0       otherwise

        """

        # Construct dense row vector
        integ_vector = np.zeros(self.coeff_size, dtype=self.coeff_dtype)
        integ_vector[0] = 2. * np.pi
        integ_vector *= self._grid_stretch

        return integ_vector

    def interpolate(self, cdata, cint, position, axis):
        """Interpolate in interval using coefficients."""

        # Contract coefficients with basis function evaluations
        dim = len(cdata.shape)
        weights = reshape_vector(self.interp_vector(position), dim=dim, axis=axis)
        if self.grid_dtype == np.float64:
            pos_interp = np.sum(cdata * weights, axis=axis, keepdims=True)
            interpolation = pos_interp + pos_interp.conj()
        elif self.grid_dtype == np.complex128:
            interpolation = np.sum(cdata * weights, axis=axis, keepdims=True)

        cint.fill(0)
        np.copyto(cint[axslice(axis, 0, 1)], interpolation)

    @CachedMethod
    def interp_vector(self, position):
        """
        Interpolation row vector.

        F_n(x) = exp(i k_n x)

        """

        # Construct dense row vector
        x = position - self.interval[0]
        interp_vector = np.exp(1j * self.wavenumbers * x)
        if self.grid_dtype == np.float64:
            interp_vector[0] /= 2

        return interp_vector

    def trans_diff(self, i):
        """Transverse differentation constant for i-th term."""

        return 1j * self.wavenumbers[i]


class Compound(ImplicitBasis):
    """Compound basis joining adjascent subbases."""

    def __init__(self, subbases, name=None):

        # Check intervals
        for i in range(len(subbases)-1):
            if subbases[i].interval[1] != subbases[i+1].interval[0]:
                raise ValueError("Subbases not adjascent.")

        # Atributes
        self.subbases = subbases
        self.element_label = "(%s)" %",".join([basis.element_label for basis in self.subbases])
        self.base_grid_size = sum(basis.base_grid_size for basis in subbases)
        self.interval = (subbases[0].interval[0], subbases[-1].interval[-1])
        self.name = name

    @property
    def library(self):
        return tuple(basis.library for basis in self.subbases)

    @library.setter
    def library(self, value):
        for basis in self.subbases:
            basis.library = value

    @CachedMethod
    def grid(self, scale=1.):
        """Build compound grid."""

        return np.concatenate([basis.grid(scale) for basis in self.subbases])

    def set_dtype(self, grid_dtype):
        """Determine coefficient properties from grid dtype."""

        # Ensure subbases return same coeff dtype
        coeff_dtypes = list(basis.set_dtype(grid_dtype) for basis in self.subbases)
        if len(set(coeff_dtypes)) > 1:
            raise ValueError("Bases returned different coeff_dtypes.")
        self.grid_dtype = np.dtype(grid_dtype)
        self.coeff_dtype = coeff_dtypes[0]
        # Sum subbasis coeff sizes
        self.coeff_size = sum(basis.coeff_size for basis in self.subbases)
        self.elements = np.arange(self.coeff_size)

        return self.coeff_dtype

    def coeff_start(self, index):
        return sum(b.coeff_size for b in self.subbases[:index])

    def grid_start(self, index, scale):
        return sum(b.grid_size(scale) for b in self.subbases[:index])

    def sub_gdata(self, gdata, index, axis):
        """Retreive gdata corresponding to one subbasis."""

        # Infer scale from gdata size
        scale = gdata.shape[axis] / self.base_grid_size
        start = self.grid_start(index, scale)
        end = self.grid_start(index+1, scale)
        return gdata[axslice(axis, start, end)]

    def sub_cdata(self, cdata, index, axis):
        """Retrieve cdata corresponding to one subbasis."""

        start = self.coeff_start(index)
        end = self.coeff_start(index+1)
        return cdata[axslice(axis, start, end)]

    def forward(self, gdata, *, axis, cdata=None):
        """Forward transforms."""

        cdata, gdata = self.check_arrays(cdata, gdata, axis)
        for index, basis in enumerate(self.subbases):
            # Transform continuous copy of subbasis gdata
            # (Transforms generally require continuous data)
            temp = fftw.create_copy(self.sub_gdata(gdata, index, axis))
            temp = basis.forward(temp, axis=axis)
            np.copyto(self.sub_cdata(cdata, index, axis), temp)

        return cdata

    def backward(self, cdata, *, axis, gdata=None, scale=1.):
        """Backward transforms."""

        cdata, gdata = self.check_arrays(cdata, gdata, axis, scale)
        for index, basis in enumerate(self.subbases):
            # Transform continuous copy of subbasis cdata
            # (Transforms generally require continuous data)
            temp = fftw.create_copy(self.sub_cdata(cdata, index, axis))
            temp = basis.backward(temp, axis=axis, scale=scale)
            np.copyto(self.sub_gdata(gdata, index, axis), temp)

        return gdata

    def differentiate(self, cdata, cderiv, axis):

        for i,b in enumerate(self.subbases):
            b_cdata = self.coeff_subdata(cdata, i, axis)
            b_cderiv = self.coeff_subdata(cderiv, i, axis)
            b.differentiate(b_cdata, b_cderiv, axis)

    @CachedAttribute
    def Pre(self):

        Pre = sparse.block_diag([b.Pre for b in self.subbases])
        return Pre.tocsr()

    @CachedAttribute
    def Diff(self):

        Diff = sparse.block_diag([b.Diff for b in self.subbases])
        return Diff.tocsr()

    @CachedMethod
    def Mult(self, p, subindex):

        size = self.coeff_size
        Mult = sparse.lil_matrix((size, size), dtype=self.coeff_dtype)
        start = self.coeff_start[subindex]
        end = self.coeff_start[subindex+1]
        subMult = self.subbases[subindex].Mult(p, 0)
        Mult[start:end, start:end] = subMult

        return Mult.tocsr()

    @CachedAttribute
    def left_vector(self):

        # Construct dense column vector
        left_vector = np.zeros(self.coeff_size, dtype=self.coeff_dtype)
        # Use first basis for BC
        start = self.coeff_start[0]
        end = self.coeff_start[1]
        left_vector[start:end] = self.subbases[0].left_vector

        return left_vector

    @CachedAttribute
    def right_vector(self):

        # Construct dense column vector
        right_vector = np.zeros(self.coeff_size, dtype=self.coeff_dtype)
        # Use last basis for BC
        start = self.coeff_start[-2]
        end = self.coeff_start[-1]
        right_vector[start:] = self.subbases[-1].right_vector

        return right_vector

    @CachedAttribute
    def integ_vector(self):

        integ_vector = np.concatenate([b.integ_vector for b in self.subbases])
        return integ_vector

    @CachedMethod
    def interp_vector(self, position):

        # Construct dense row vector
        interp_vector = np.zeros(self.coeff_size, dtype=self.coeff_dtype)
        # Take first basis with position in interval
        for i,b in enumerate(self.subbases):
            if b.interval[0] <= position <= b.interval[1]:
                start = self.coeff_start[i]
                end = self.coeff_start[i+1]
                interp_vector[start:end] = b.interp_vector(position)
                return interp_vector
        raise ValueError("Position outside any subbasis interval.")

    @CachedAttribute
    def bc_vector(self):

        # Construct dense column vector
        bc_vector = np.zeros((self.coeff_size, 1), dtype=self.coeff_dtype)
        # Use last basis spot for BC
        start = self.coeff_start[-2]
        end = self.coeff_start[-1]
        bc_vector[start:end] = self.subbases[-1].bc_vector

        return bc_vector

    @CachedAttribute
    def match_vector(self):

        # Construct dense column vector
        match_vector = np.zeros((self.coeff_size, 1), dtype=self.coeff_dtype)
        # Use all but last basis spots for matching
        for i,b in enumerate(self.subbases[:-1]):
            start = self.coeff_start[i]
            end = self.coeff_start[i+1]
            match_vector[start:end] = b.bc_vector

        return match_vector

    @CachedAttribute
    def Match(self):

        size = self.coeff_size
        Match = sparse.lil_matrix((size, size), dtype=self.coeff_dtype)
        for i in range(len(self.subbases) - 1):
            basis1 = self.subbases[i]
            basis2 = self.subbases[i+1]
            s1 = self.coeff_start[i]
            e1 = self.coeff_start[i+1]
            s2 = e1
            e2 = self.coeff_start[i+2]

            k1 = sparse.kron(basis1.bc_vector, basis1.right_vector)
            Match[s1:e1, s1:e1] = sparse.kron(basis1.bc_vector, basis1.right_vector)
            Match[s1:e1, s2:e2] = -sparse.kron(basis1.bc_vector, basis2.left_vector)

        return Match.tocsr()

