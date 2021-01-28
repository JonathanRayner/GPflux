# Copyright (C) PROWLER.io 2018 - All Rights Reserved
# Unauthorized copying of this file, via any medium is strictly prohibited
# Proprietary and confidential
"""A Sparse Variational Multioutput Gaussian Process Keras Layer"""

from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import tensorflow as tf
import tensorflow_probability as tfp
from tensorflow_probability.python.layers import DistributionLambda

from gpflow import Parameter, default_float, default_jitter
from gpflow.base import TensorType
from gpflow.conditionals import conditional
from gpflow.inducing_variables import MultioutputInducingVariables
from gpflow.kernels import MultioutputKernel
from gpflow.kullback_leiblers import prior_kl
from gpflow.mean_functions import Identity, MeanFunction
from gpflow.utilities.bijectors import triangular

from gpflux.exceptions import GPInitializationError
from gpflux.initializers import FeedForwardInitializer, Initializer
from gpflux.sampling.sample import Sample, efficient_sample
from gpflux.types import ShapeType
from gpflux.utils.runtime_checks import verify_compatibility


class GPLayer(DistributionLambda):
    """A sparse variational multioutput GP layer"""

    def __init__(
        self,
        kernel: MultioutputKernel,
        inducing_variable: MultioutputInducingVariables,
        num_data: int,
        initializer: Optional[Initializer] = None,
        mean_function: Optional[MeanFunction] = None,
        *,
        num_samples: Optional[int] = None,
        full_output_cov: bool = False,
        full_cov: bool = False,
        verify: bool = True,
        num_latent_gps: int = None,
        white: bool = True,
    ):
        """
        A sparse variational GP layer in whitened representation. This layer holds the
        kernel, variational parameters, inducing points and mean function.

        :param kernel: The multioutput kernel for the layer
        :param inducing_variable: The inducing features for the layer
        :param initializer: the initializer for the inducing variables and variational
            parameters. Default: FeedForwardInitializer
        :param mean_function: The mean function applied to the inputs. Default: Identity

        :param num_samples: the number of samples S to draw when converting the
            DistributionLambda into a tensor. By default, draw a single sample without prefixing
            sample shape (see tfp's Distribution.sample()).
        :param full_cov: Use a full covariance in predictions, or just the diagonals
        :param full_output_cov: Return a full output covariance
        :param verify: if False, the call to `verify_compatibility` in the init is bypassed.
            The user is then responsible for making sure `kernel`, `mean_function`
            and `inducing_variable` are compatible and work togheter. It is also required
            to specify `num_latent_gps`, as this will not be infered from the other objects.
        :param num_latent_gps: number of (latent) GPs in the layer. Used to determine the size of
            the variational parameters `q_mu` and `q_sqrt`. Only required to be passed when
            `verify` is set to False, otherwise it is infered from the `kernel` and
            `inducing_variable`.
        :param white: determines the parameterisation of the inducing variables.
            If True: p(u) = N(0, I), else p(u) = N(0, Kuu).
            TODO(VD): The initializer currently only support white = True.
        """

        super().__init__(
            make_distribution_fn=self._make_distribution_fn,
            convert_to_tensor_fn=self._convert_to_tensor_fn,
            dtype=default_float(),
        )

        if initializer is None:
            initializer = FeedForwardInitializer()
        if mean_function is None:
            mean_function = Identity()

        self.kernel = kernel
        self.inducing_variable = inducing_variable
        self.initializer = initializer
        self.mean_function = mean_function

        self.full_output_cov = full_output_cov
        self.full_cov = full_cov
        self.num_data = num_data
        self.white = white

        if verify:
            self.num_inducing, self.num_latent_gps = verify_compatibility(
                kernel, mean_function, inducing_variable
            )
        else:
            self.num_inducing, self.num_latent_gps = (
                len(inducing_variable),
                num_latent_gps,
            )

        self.q_mu = Parameter(
            np.zeros((self.num_inducing, self.num_latent_gps)), dtype=default_float(), name="q_mu",
        )  # [num_inducing, output_dim]

        self.q_sqrt = Parameter(
            np.stack([np.eye(self.num_inducing) for _ in range(self.num_latent_gps)]),
            transform=triangular(),
            dtype=default_float(),
            name="q_sqrt",
        )  # [output_dim, num_inducing, num_inducing]
        self._initialized = False

        self._num_samples = num_samples

    def initialize_inducing_variables(self, **initializer_kwargs: Any) -> None:
        if self._initialized:
            raise GPInitializationError("Initializing twice!")

        self.initializer.init_inducing_variable(self.inducing_variable, **initializer_kwargs)
        self._initialized = True

    def build(self, input_shape: ShapeType) -> None:
        """Build the variables necessary on first call"""

        super().build(input_shape)
        if not self.initializer.init_at_predict:
            self.initialize_inducing_variables()

    def predict(
        self,
        inputs: TensorType,
        *,
        full_output_cov: bool = False,
        full_cov: bool = False,
        white: bool = True,
    ) -> Tuple[TensorType, TensorType]:
        """
        Make a prediction at N test inputs, with input_dim = D, output_dim = Q. Return the
        conditional mean and covariance at these points.

        :param inputs: the inputs to predict at. shape [N, D]
        :param full_output_cov: If true: return the full covariance between Q ouput
            dimensions. Cov shape: -> [N, Q, N, Q]. If false: return block diagonal
            covariances. Cov shape: -> [Q, N, N]
        :param full_cov: If true: return the full (NxN) covariance for each output
            dimension Q.  Cov shape -> [Q, N, N]. If false: return variance (N) at each
            output dimension Q. Cov shape -> [N, Q]
        :param white:
        """
        if (
            inputs.shape[0] is not None  # do not initialize for symbolic tensors
            and self.initializer.init_at_predict
            and not self._initialized
        ):
            self.initialize_inducing_variables(inputs=inputs)

        mean_function = self.mean_function(inputs)
        mean_cond, cov = conditional(
            inputs,
            self.inducing_variable,
            self.kernel,
            self.q_mu,
            q_sqrt=self.q_sqrt,
            full_cov=full_cov,
            full_output_cov=full_output_cov,
            white=white,
        )

        return mean_cond + mean_function, cov

    def call(self, inputs: TensorType, *args: List[Any], **kwargs: Dict[str, Any]) -> TensorType:
        """
        The default behaviour upon calling the GPLayer()(X).

        This method calls the `DistributionLambda` super-class `call` method, which will construct
        a TensorFlow Probability distribution for the marginal distributions at the input points.
        This distribution can be passed to `tf.convert_to_tensor`, which will return samples from
        the distribution.

        This method also adds a layer-specific loss function, given by the KL divergence between
        this layer and the GP prior.
        """
        outputs = super().call(inputs, *args, **kwargs)

        # TF quirk: add_loss must add a tensor to compile
        if kwargs.get("training"):
            loss = self.prior_kl(whiten=self.white)
        else:
            loss = tf.constant(0.0, dtype=default_float())
        loss_per_datapoint = loss / self.num_data

        self.add_loss(loss_per_datapoint)
        self.add_metric(loss_per_datapoint, name="elbo_kl_gp", aggregation="mean")

        return outputs

    def prior_kl(self, whiten: bool = True) -> TensorType:
        """
        The KL divergence from the variational distribution to the prior

        :param whiten:
        :return: KL divergence from N(q_mu, q_sqrt) to N(0, I) independently for each GP
        """
        return prior_kl(self.inducing_variable, self.kernel, self.q_mu, self.q_sqrt, whiten=whiten)

    def _make_distribution_fn(
        self, previous_layer_outputs: TensorType
    ) -> tfp.distributions.Distribution:
        """
        Compute the marginal distributions at the output points of the previous layer.

        :param previous_layer_outputs: The output from the previous layer, which can be coerced
                                       to a Tensor.
        """
        if self.full_cov and self.full_output_cov:
            msg = "The combination of both `full_cov` and `full_output_cov` is not permitted."
            raise NotImplementedError(msg)

        mean, cov = self.predict(
            previous_layer_outputs,
            full_cov=self.full_cov,
            full_output_cov=self.full_output_cov,
            white=self.white,
        )

        if self.full_cov:
            return tfp.distributions.MultivariateNormalTriL(
                loc=tf.linalg.adjoint(mean), scale_tril=_cholesky_with_jitter(cov)
            )
        elif self.full_output_cov:
            return tfp.distributions.MultivariateNormalTriL(
                loc=mean, scale_tril=_cholesky_with_jitter(cov)
            )
        else:
            return tfp.distributions.MultivariateNormalDiag(loc=mean, scale_diag=cov)

    def _convert_to_tensor_fn(self, distribution: tfp.distributions.Distribution) -> TensorType:
        """
        This method converts the marginal distribution at the N input points to a tensor of (S)
        samples with output_dim Q from that distribution.
        """
        if self._num_samples is not None:
            samples = distribution.sample((self._num_samples,))  # [S, N, Q]
        else:
            samples = distribution.sample()  # [N, Q]

        if self.full_cov:
            samples = tf.linalg.adjoint(samples)  # [N, Q]

        return samples

    def sample(self) -> Sample:
        return (
            efficient_sample(
                self.inducing_variable,
                self.kernel,
                self.q_mu,
                q_sqrt=self.q_sqrt,
                white=self.white,
            )
            # Makes use of the magic __add__ of the Sample class
            + self.mean_function
        )


def _cholesky_with_jitter(cov: TensorType) -> TensorType:
    """
    Compute the Cholesky of the covariance, adding jitter to the diagonal to improve stability.

    :param cov: Full covariance with shape [..., N, D, D].
    """
    # cov [..., N, D, D]
    cov_shape = tf.shape(cov)
    N = cov_shape[:-3]
    D = cov_shape[-2]
    jittermat = tf.eye(D, batch_shape=N, dtype=cov.dtype) * default_jitter()  # [..., N, D, D]
    return tf.linalg.cholesky(cov + jittermat)  # [..., N, D, D]
