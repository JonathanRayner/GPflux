#
# Copyright (c) 2021 The GPflux Contributors.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#
"""
This module provides a set of common utilities for kernel feature decompositions.
"""
from typing import Tuple, Type

import tensorflow as tf

import gpflow
from gpflow.base import DType, TensorType

from gpflux.types import ShapeType

RFF_SUPPORTED_KERNELS: Tuple[Type[gpflow.kernels.Stationary], ...] = (
    gpflow.kernels.SquaredExponential,
    gpflow.kernels.Matern12,
    gpflow.kernels.Matern32,
    gpflow.kernels.Matern52,
)


def _matern_number(kernel: gpflow.kernels.Kernel) -> int:
    if isinstance(kernel, gpflow.kernels.Matern52):
        p = 2
    elif isinstance(kernel, gpflow.kernels.Matern32):
        p = 1
    elif isinstance(kernel, gpflow.kernels.Matern12):
        p = 0
    else:
        raise NotImplementedError("Not a recognized Matern kernel")
    return p


def _sample_students_t(nu: float, shape: ShapeType, dtype: DType) -> TensorType:
    """
    Draw samples from a (central) Student's t-distribution using the following:
      BETA ~ Gamma(nu/2, nu/2) (shape-rate parameterization)
      X ~ Normal(0, 1/BETA)
    then:
      X ~ StudentsT(nu)

    Note this is equivalent to the more commonly used parameterization
      Z ~ Chi2(nu) = Gamma(nu/2, 1/2)
      EPSILON ~ Normal(0, 1)
      X = EPSILON * sqrt(nu/Z)

    To see this, note
      Z/nu ~ Gamma(nu/2, nu/2)
    and
      X ~ Normal(0, nu/Z)
    The equivalence becomes obvious when we set BETA = Z/nu
    """
    # Normal(0, 1)
    normal_rvs = tf.random.normal(shape=shape, dtype=dtype)
    shape = tf.concat([shape[:-1], [1]], axis=0)
    # Gamma(nu/2, nu/2)
    gamma_rvs = tf.random.gamma(shape, alpha=0.5 * nu, beta=0.5 * nu, dtype=dtype)
    # StudentsT(nu)
    students_t_rvs = tf.math.rsqrt(gamma_rvs) * normal_rvs
    return students_t_rvs


def _mapping_cosine(
    X: TensorType,
    W: TensorType,
    b: TensorType,
    variance: TensorType,
    lengthscales: TensorType,
    n_components: int,
) -> TensorType:
    """
    Feature map for random Fourier features (RFF) as originally prescribed
    by Rahimi & Recht, 2007 :cite:p:`rahimi2007random`.
    See also :cite:p:`sutherland2015error` for additional details.
    """
    constant = tf.sqrt(2.0 * variance / n_components)
    X_scaled = tf.divide(X, lengthscales)  # [N, D]
    proj = tf.matmul(X_scaled, W, transpose_b=True)  # [N, M]
    bases = tf.cos(proj + b)  # [N, M]
    return constant * bases  # [N, M]


def _mapping_concat(
    X: TensorType,
    W: TensorType,
    variance: TensorType,
    lengthscales: TensorType,
    n_components: int,
) -> TensorType:
    """
    Feature map for random Fourier features (RFF) as originally prescribed
    by Rahimi & Recht, 2007 :cite:p:`rahimi2007random`.
    See also :cite:p:`sutherland2015error` for additional details.
    """
    constant = tf.sqrt(2.0 * variance / n_components)
    X_scaled = tf.divide(X, lengthscales)  # [N, D]
    proj = tf.matmul(X_scaled, W, transpose_b=True)  # [N, M // 2]
    bases = tf.concat([tf.sin(proj), tf.cos(proj)], axis=-1)  # [N, M]
    return constant * bases  # [N, M]