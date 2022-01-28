#   Copyright 2020 The PyMC Developers
#
#   Licensed under the Apache License, Version 2.0 (the "License");
#   you may not use this file except in compliance with the License.
#   You may obtain a copy of the License at
#
#       http://www.apache.org/licenses/LICENSE-2.0
#
#   Unless required by applicable law or agreed to in writing, software
#   distributed under the License is distributed on an "AS IS" BASIS,
#   WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#   See the License for the specific language governing permissions and
#   limitations under the License.

from typing import Optional, Union

import aesara.tensor as at
import numpy as np

from aesara import scan
from aesara.tensor.random.op import RandomVariable, default_shape_from_params

import pymc as pm

from pymc.distributions import distribution, multivariate
from pymc.distributions.continuous import Flat, Normal, get_tau_sigma
from pymc.distributions.shape_utils import to_tuple

__all__ = [
    "AR1",
    "AR",
    "GaussianRandomWalk",
    "GARCH11",
    "EulerMaruyama",
    "MvGaussianRandomWalk",
    "MvStudentTRandomWalk",
]


class GaussianRandomWalkRV(RandomVariable):
    """
    GaussianRandomWalk Random Variable
    """

    name = "GaussianRandomWalk"
    ndim_supp = 1
    ndims_params = [0, 0, 0]
    dtype = "floatX"
    _print_name = ("GaussianRandomWalk", "\\operatorname{GaussianRandomWalk}")

    def _shape_from_params(self, dist_params, reop_param_idx=1, param_shapes=None):
        # (size which is number of time series, steps)
        return (dist_params[-2], dist_params[-1])
        raise Exception("Ravin's shape exception")

        # if self.ndim_supp <= 0:
        #     raise ValueError("ndim_supp must be greater than 0")
        # if param_shapes is not None:
        #     ref_param = param_shapes[rep_param_idx]
        #     return (ref_param[-self.ndim_supp],)
        # else:
        #     ref_param = dist_params[rep_param_idx]
        #     if ref_param.ndim < self.ndim_supp:
        #         raise ValueError(
        #             (
        #                 "Reference parameter does not match the "
        #                 f"expected dimensions; {ref_param} has less than {self.ndim_supp} dim(s)."
        #             )
        #         )
        #     return ref_param.shape[-self.ndim_supp:]

    @classmethod
    def rng_fn(
        cls,
        rng: np.random.RandomState,
        mu: Union[np.ndarray, float],
        sigma: Union[np.ndarray, float],
        init: float,
        steps: int,
        size: int,
    ) -> np.ndarray:
        """Gaussian Random Walk generator.

        The init value is drawn from the Normal distribution with the same sigma as the
        innovations.

        Notes
        -----
        Currently does not support custom init distribution

        Parameters
        ----------
        rng: np.random.RandomState
           Numpy random number generator
        mu: np.ndarray
           Random walk mean
        sigma: np.ndarray
            Standard deviation of innovation (sigma > 0)
        init: float
            Initialization value for GaussianRandomWalk
        steps: int
            Length of random walk, must be greater than 1. Returned array will be of size+1 to
            account as first value is initial value
        size: int
            The number of Random Walk time series generated

        Returns
        -------
        np.ndarray
        """

        if steps is None or steps == 0:
            raise ValueError("Steps must be greater than 0 or not None")
        if size is None:
            size = 1

        init_val = rng.normal(init, sigma, size=(size, 1))
        steps = rng.normal(loc=mu, scale=sigma, size=(size, steps))
        grw = np.concatenate([init_val, steps], axis=-1)

        return np.cumsum(grw, axis=-1).squeeze()


gaussianrandomwalk = GaussianRandomWalkRV()


class AR1(distribution.Continuous):
    """
    Autoregressive process with 1 lag.

    Parameters
    ----------
    k: tensor
       effect of lagged value on current value
    tau_e: tensor
       precision for innovations
    """

    def __init__(self, k, tau_e, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.k = k = at.as_tensor_variable(k)
        self.tau_e = tau_e = at.as_tensor_variable(tau_e)
        self.tau = tau_e * (1 - k ** 2)
        self.mode = at.as_tensor_variable(0.0)

    def logp(self, x):
        """
        Calculate log-probability of AR1 distribution at specified value.

        Parameters
        ----------
        x: numeric
            Value for which log-probability is calculated.

        Returns
        -------
        TensorVariable
        """
        k = self.k
        tau_e = self.tau_e  # innovation precision
        tau = tau_e * (1 - k ** 2)  # ar1 precision

        x_im1 = x[:-1]
        x_i = x[1:]
        boundary = Normal.dist(0.0, tau=tau).logp

        innov_like = Normal.dist(k * x_im1, tau=tau_e).logp(x_i)
        return boundary(x[0]) + at.sum(innov_like)


class AR(distribution.Continuous):
    r"""
    Autoregressive process with p lags.

    .. math::

       x_t = \rho_0 + \rho_1 x_{t-1} + \ldots + \rho_p x_{t-p} + \epsilon_t,
       \epsilon_t \sim N(0,\sigma^2)

    The innovation can be parameterized either in terms of precision
    or standard deviation. The link between the two parametrizations is
    given by

    .. math::

       \tau = \dfrac{1}{\sigma^2}

    Parameters
    ----------
    rho: tensor
        Tensor of autoregressive coefficients. The first dimension is the p lag.
    sigma: float
        Standard deviation of innovation (sigma > 0). (only required if tau is not specified)
    tau: float
        Precision of innovation (tau > 0). (only required if sigma is not specified)
    constant: bool (optional, default = False)
        Whether to include a constant.
    init: distribution
        distribution for initial values (Defaults to Flat())
    """

    def __init__(
        self, rho, sigma=None, tau=None, constant=False, init=None, sd=None, *args, **kwargs
    ):
        super().__init__(*args, **kwargs)
        if sd is not None:
            sigma = sd

        tau, sigma = get_tau_sigma(tau=tau, sigma=sigma)
        self.sigma = self.sd = at.as_tensor_variable(sigma)
        self.tau = at.as_tensor_variable(tau)

        self.mean = at.as_tensor_variable(0.0)

        if isinstance(rho, list):
            p = len(rho)
        else:
            try:
                shape_ = rho.shape.tag.test_value
            except AttributeError:
                shape_ = rho.shape

            if hasattr(shape_, "size") and shape_.size == 0:
                p = 1
            else:
                p = shape_[0]

        if constant:
            self.p = p - 1
        else:
            self.p = p

        self.constant = constant
        self.rho = rho = at.as_tensor_variable(rho)
        self.init = init or Flat.dist()

    def logp(self, value):
        """
        Calculate log-probability of AR distribution at specified value.

        Parameters
        ----------
        value: numeric
            Value for which log-probability is calculated.

        Returns
        -------
        TensorVariable
        """
        if self.constant:
            x = at.add(
                *(self.rho[i + 1] * value[self.p - (i + 1) : -(i + 1)] for i in range(self.p))
            )
            eps = value[self.p :] - self.rho[0] - x
        else:
            if self.p == 1:
                x = self.rho * value[:-1]
            else:
                x = at.add(
                    *(self.rho[i] * value[self.p - (i + 1) : -(i + 1)] for i in range(self.p))
                )
            eps = value[self.p :] - x

        innov_like = Normal.dist(mu=0.0, tau=self.tau).logp(eps)
        init_like = self.init.logp(value[: self.p])

        return at.sum(innov_like) + at.sum(init_like)


class GaussianRandomWalk(distribution.Continuous):
    r"""Random Walk with Normal innovations


    Notes
    -----
    init is currently drawn from a Normal distribution with the same sigma as the innovations

    Parameters
    ----------
    mu: tensor
        innovation drift, defaults to 0.0
    sigma: tensor
        sigma > 0, innovation standard deviation, defaults to 0.0
    init: float
        Mean value of initialization, defaults to 0.0
    """

    rv_op = gaussianrandomwalk

    @classmethod
    def dist(
        cls,
        mu: Optional[Union[np.ndarray, float]] = 0.0,
        sigma: Optional[Union[np.ndarray, float]] = 1.0,
        init: float = 0.0,
        size: int = None,
        steps: int = 0,
        *args,
        **kwargs
    ) -> RandomVariable:

        return super().dist([mu, sigma, init, steps], **kwargs)

    def logp(
        value: at.Variable,
        mu: at.Variable,
        sigma: at.Variable,
        init: at.Variable,
    ) -> at.TensorVariable:
        """Calculate log-probability of Gaussian Random Walk distribution at specified value.

        Parameters
        ----------
        value: at.Variable,
        mu: at.Variable,
        sigma: at.Variable,
        init: at.Variable,

        Returns
        -------
        TensorVariable
        """

        # Calculate initialization logp
        init_logp = pm.logp(Normal.dist(init, sigma), 0)

        # Make time series stationary around the mean value
        stationary_series = at.diff(value)
        series_logp = pm.logp(Normal.dist(mu, sigma), stationary_series)

        total_logp = at.concatenate([at.expand_dims(init_logp, 0), series_logp])

        return total_logp


class GARCH11(distribution.Continuous):
    r"""
    GARCH(1,1) with Normal innovations. The model is specified by

    .. math::
        y_t = \sigma_t * z_t

    .. math::
        \sigma_t^2 = \omega + \alpha_1 * y_{t-1}^2 + \beta_1 * \sigma_{t-1}^2

    with z_t iid and Normal with mean zero and unit standard deviation.

    Parameters
    ----------
    omega: tensor
        omega > 0, mean variance
    alpha_1: tensor
        alpha_1 >= 0, autoregressive term coefficient
    beta_1: tensor
        beta_1 >= 0, alpha_1 + beta_1 < 1, moving average term coefficient
    initial_vol: tensor
        initial_vol >= 0, initial volatility, sigma_0
    """

    def __init__(self, omega, alpha_1, beta_1, initial_vol, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.omega = omega = at.as_tensor_variable(omega)
        self.alpha_1 = alpha_1 = at.as_tensor_variable(alpha_1)
        self.beta_1 = beta_1 = at.as_tensor_variable(beta_1)
        self.initial_vol = at.as_tensor_variable(initial_vol)
        self.mean = at.as_tensor_variable(0.0)

    def get_volatility(self, x):
        x = x[:-1]

        def volatility_update(x, vol, w, a, b):
            return at.sqrt(w + a * at.square(x) + b * at.square(vol))

        vol, _ = scan(
            fn=volatility_update,
            sequences=[x],
            outputs_info=[self.initial_vol],
            non_sequences=[self.omega, self.alpha_1, self.beta_1],
        )
        return at.concatenate([[self.initial_vol], vol])

    def logp(self, x):
        """
        Calculate log-probability of GARCH(1, 1) distribution at specified value.

        Parameters
        ----------
        x: numeric
            Value for which log-probability is calculated.

        Returns
        -------
        TensorVariable
        """
        vol = self.get_volatility(x)
        return at.sum(Normal.dist(0.0, sigma=vol).logp(x))

    def _distr_parameters_for_repr(self):
        return ["omega", "alpha_1", "beta_1"]


class EulerMaruyama(distribution.Continuous):
    r"""
    Stochastic differential equation discretized with the Euler-Maruyama method.

    Parameters
    ----------
    dt: float
        time step of discretization
    sde_fn: callable
        function returning the drift and diffusion coefficients of SDE
    sde_pars: tuple
        parameters of the SDE, passed as ``*args`` to ``sde_fn``
    """

    def __init__(self, dt, sde_fn, sde_pars, *args, **kwds):
        super().__init__(*args, **kwds)
        self.dt = dt = at.as_tensor_variable(dt)
        self.sde_fn = sde_fn
        self.sde_pars = sde_pars

    def logp(self, x):
        """
        Calculate log-probability of EulerMaruyama distribution at specified value.

        Parameters
        ----------
        x: numeric
            Value for which log-probability is calculated.

        Returns
        -------
        TensorVariable
        """
        xt = x[:-1]
        f, g = self.sde_fn(x[:-1], *self.sde_pars)
        mu = xt + self.dt * f
        sd = at.sqrt(self.dt) * g
        return at.sum(Normal.dist(mu=mu, sigma=sd).logp(x[1:]))

    def _distr_parameters_for_repr(self):
        return ["dt"]


class MvGaussianRandomWalk(distribution.Continuous):
    r"""
    Multivariate Random Walk with Normal innovations

    Parameters
    ----------
    mu: tensor
        innovation drift, defaults to 0.0
    cov: tensor
        pos def matrix, innovation covariance matrix
    tau: tensor
        pos def matrix, inverse covariance matrix
    chol: tensor
        Cholesky decomposition of covariance matrix
    init: distribution
        distribution for initial value (Defaults to Flat())

    Notes
    -----
    Only one of cov, tau or chol is required.

    """

    def __init__(
        self, mu=0.0, cov=None, tau=None, chol=None, lower=True, init=None, *args, **kwargs
    ):
        super().__init__(*args, **kwargs)

        self.init = init or Flat.dist()
        self.innovArgs = (mu, cov, tau, chol, lower)
        self.innov = multivariate.MvNormal.dist(*self.innovArgs, shape=self.shape)
        self.mean = at.as_tensor_variable(0.0)

    def logp(self, x):
        """
        Calculate log-probability of Multivariate Gaussian
        Random Walk distribution at specified value.

        Parameters
        ----------
        x: numeric
            Value for which log-probability is calculated.

        Returns
        -------
        TensorVariable
        """

        if x.ndim == 1:
            x = x[np.newaxis, :]

        x_im1 = x[:-1]
        x_i = x[1:]

        return self.init.logp_sum(x[0]) + self.innov.logp_sum(x_i - x_im1)

    def _distr_parameters_for_repr(self):
        return ["mu", "cov"]

    def random(self, point=None, size=None):
        """
        Draw random values from MvGaussianRandomWalk.

        Parameters
        ----------
        point: dict, optional
            Dict of variable values on which random values are to be
            conditioned (uses default point if not specified).
        size: int or tuple of ints, optional
            Desired size of random sample (returns one sample if not
            specified).

        Returns
        -------
        array


        Examples
        -------

        .. code-block:: python

            with pm.Model():
                mu = np.array([1.0, 0.0])
                cov = np.array([[1.0, 0.0],
                                [0.0, 2.0]])

                # draw one sample from a 2-dimensional Gaussian random walk with 10 timesteps
                sample = MvGaussianRandomWalk(mu, cov, shape=(10, 2)).random()

                # draw three samples from a 2-dimensional Gaussian random walk with 10 timesteps
                sample = MvGaussianRandomWalk(mu, cov, shape=(10, 2)).random(size=3)

                # draw four samples from a 2-dimensional Gaussian random walk with 10 timesteps,
                # indexed with a (2, 2) array
                sample = MvGaussianRandomWalk(mu, cov, shape=(10, 2)).random(size=(2, 2))

        """

        # for each draw specified by the size input, we need to draw time_steps many
        # samples from MvNormal.

        size = to_tuple(size)
        multivariate_samples = self.innov.random(point=point, size=size)
        # this has shape (size, self.shape)
        if len(self.shape) == 2:
            # have time dimension in first slot of shape. Therefore the time
            # component can be accessed with the index equal to the length of size.
            time_axis = len(size)
            multivariate_samples = multivariate_samples.cumsum(axis=time_axis)
            if time_axis != 0:
                # this for loop covers the case where size is a tuple
                for idx in np.ndindex(size):
                    multivariate_samples[idx] = (
                        multivariate_samples[idx] - multivariate_samples[idx][0]
                    )
            else:
                # size was passed as None
                multivariate_samples = multivariate_samples - multivariate_samples[0]

        # if the above statement fails, then only a spatial dimension was passed in for self.shape.
        # Therefore don't subtract off the initial value since otherwise you get all zeros
        # as your output.
        return multivariate_samples


class MvStudentTRandomWalk(MvGaussianRandomWalk):
    r"""
    Multivariate Random Walk with StudentT innovations

    Parameters
    ----------
    nu: degrees of freedom
    mu: tensor
        innovation drift, defaults to 0.0
    cov: tensor
        pos def matrix, innovation covariance matrix
    tau: tensor
        pos def matrix, inverse covariance matrix
    chol: tensor
        Cholesky decomposition of covariance matrix
    init: distribution
        distribution for initial value (Defaults to Flat())
    """

    def __init__(self, nu, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.nu = at.as_tensor_variable(nu)
        self.innov = multivariate.MvStudentT.dist(self.nu, None, *self.innovArgs)

    def _distr_parameters_for_repr(self):
        return ["nu", "mu", "cov"]
