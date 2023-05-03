"""Bijections that wrap jax function transforms (scan and vmap)."""
from functools import partial
from typing import Any, Tuple

import equinox as eqx
from jax.lax import scan

from flowjax.bijections.bijection import Bijection


class Vmap(Bijection):
    """Expand the dimension of a bijection by vmapping. By default, we vmap over both the
    bijection parameters and x, although this behaviour can be modified by providing key
    word arguments that are passed to ``equinox.filter_vmap``. The arguments names for
    the vmapped functions are (bijection, x).

    Vmapping over the conditioning variable is not currently supported.

    Example:
        Affine parameters usually act elementwise, but we could vmap excluding the
        the bijection to create a global affine (sharing the location and scale).

        .. doctest::

            >>> from flowjax.bijections import Vmap, Affine
            >>> import jax.numpy as jnp
            >>> affine = Vmap(Affine(1), (3,), kwargs=dict(bijection=None))
            >>> affine.transform(jnp.ones(3))
            Array([2., 2., 2.], dtype=float32)

    """

    ndim_to_add: int
    bijection: Bijection
    kwargs: dict

    def __init__(self, bijection: Bijection, shape: Tuple[int, ...], **kwargs):
        """
        Args:
            bijection (Bijection): Bijection. If vmapping over the bijection, the array
                leaves in bijection should have additional leading axes with shape
                equalling `shape`. Often it is convenient to construct these using
                `equinox.filter_vmap`.
            shape (Tuple[int, ...]): Shape prepended to the bijection shape. If
                len(shape)>1, multiple applications of vmap will be used.
            **kwargs: kwargs, passed to equinox.filter_vmap, allowing e.g. control over
                which variables to map over.
        """
        self.bijection = bijection
        self.ndim_to_add = len(shape)
        self.kwargs = kwargs  # For filter vmap
        self.shape = (
            shape + self.bijection.shape if self.bijection.shape is not None else None
        )
        self.cond_shape = bijection.cond_shape

    def transform(self, x, condition=None):
        self._argcheck(x, condition)

        def _transform(bijection, x):
            return bijection.transform(x, condition)

        _transform = self._multivmap(_transform)
        return _transform(self.bijection, x)

    def inverse(self, y, condition=None):
        self._argcheck(y, condition)

        def _inverse(bijection, x):
            return bijection.inverse(x, condition)

        _inverse = self._multivmap(_inverse)
        return _inverse(self.bijection, y)

    def transform_and_log_abs_det_jacobian(self, x, condition=None):
        self._argcheck(x, condition)

        def _transform_and_log_det(bijection, x):
            return bijection.transform_and_log_abs_det_jacobian(x, condition)

        _transform_and_log_det = self._multivmap(_transform_and_log_det)
        y, log_det = _transform_and_log_det(self.bijection, x)
        return y, log_det.sum()

    def inverse_and_log_abs_det_jacobian(self, y, condition=None):
        self._argcheck(y, condition)

        def _inv_and_log_det(bijection, x):
            return bijection.inverse_and_log_abs_det_jacobian(x, condition)

        _inv_and_log_det = self._multivmap(_inv_and_log_det)
        x, log_det = _inv_and_log_det(self.bijection, y)
        return x, log_det.sum()

    def _multivmap(self, func):
        """Compose Vmap to add ndim batch dimensions."""
        for _ in range(self.ndim_to_add):
            func = eqx.filter_vmap(func, **self.kwargs)
        return func


class Scan(Bijection):
    """Repeatedly apply the same bijection with different parameter values. Internally,
    uses `jax.lax.scan` to reduce compilation time.
    """

    static: Any
    params: Any

    def __init__(self, bijection: Bijection):
        """
        The array leaves in `bijection` should have an additional leading axis to scan over.
        Often it is convenient to construct these using `equinox.filter_vmap`.

        Args:
            bijection (Bijection): A bijection, in which the arrays leaves have an
                additional leading axis to scan over. For complex bijections, it can be
                convenient to create compatible bijections with ``equinox.filter_vmap``.

        Example:
            Below is equivilent to ``Chain([Affine(p) for p in params])``.

            .. doctest::

                >>> from flowjax.bijections import Scan, Affine
                >>> import jax.numpy as jnp
                >>> import equinox as eqx
                >>> params = jnp.ones((3, 2))
                >>> affine = Scan(Affine(params))

        """
        self.params, self.static = eqx.partition(bijection, eqx.is_array)  # type: ignore
        self.shape = bijection.shape
        self.cond_shape = bijection.cond_shape

    def transform(self, x, condition=None):
        self._argcheck(x, condition)

        def fn(x, p, condition=None):
            bijection = eqx.combine(self.static, p)
            result = bijection.transform(x, condition)  # type: ignore
            return (result, None)

        fn = partial(fn, condition=condition)
        y, _ = scan(fn, x, self.params)
        return y

    def transform_and_log_abs_det_jacobian(self, x, condition=None):
        self._argcheck(x, condition)

        def fn(carry, params, condition):
            x, log_det = carry
            bijection = eqx.combine(self.static, params)
            y, log_det_i = bijection.transform_and_log_abs_det_jacobian(  # type: ignore
                x, condition
            )
            return ((y, log_det + log_det_i.sum()), None)

        fn = partial(fn, condition=condition)
        (y, log_det), _ = scan(fn, (x, 0), self.params)
        return y, log_det

    def inverse(self, y, condition=None):
        self._argcheck(y, condition)

        def fn(y, params, condition=None):
            bijection = eqx.combine(self.static, params)
            x = bijection.inverse(y, condition)  # type: ignore
            return (x, None)

        fn = partial(fn, condition=condition)
        x, _ = scan(fn, y, self.params, reverse=True)
        return x

    def inverse_and_log_abs_det_jacobian(self, y, condition=None):
        self._argcheck(y, condition)

        def fn(carry, params, condition=None):
            y, log_det = carry
            bijection = eqx.combine(self.static, params)
            x, log_det_i = bijection.inverse_and_log_abs_det_jacobian(  # type: ignore
                y, condition
            )
            return ((x, log_det + log_det_i.sum()), None)

        fn = partial(fn, condition=condition)
        (y, log_det), _ = scan(fn, (y, 0), self.params, reverse=True)
        return y, log_det
