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
""" This module provides the base implementation for Distributional DeepGP models. """

from gpflux.models import DeepGP


class DistDeepGP(DeepGP):

    """
    This class combines a sequential function model ``f(x) = fₙ(⋯ (f₂(f₁(x))))``
    and a likelihood ``p(y|f)``
    """

    def __repr__(self):
        return f'DistDGP(layers:"{len(self.f_layers)}",units:"{self.f_layers[0].num_latent_gps}",lik.:{self.likelihood_layer})'