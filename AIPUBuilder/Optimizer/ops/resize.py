# SPDX-License-Identifier: Apache-2.0
# Copyright © 2023 Arm Technology (China) Co. Ltd.

from AIPUBuilder.Optimizer.framework import *

from AIPUBuilder.Optimizer.ops.interp import *


@op_register(OpType.Resize)
def resize(self, *args):
    return interp(self, *args)


@quant_register(OpType.Resize)
def resize_quantize(self, *args):
    interp_quantize(self, *args)
