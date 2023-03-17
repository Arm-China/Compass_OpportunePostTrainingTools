# Copyright © 2023 Arm Technology (China) Co. Ltd. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

from AIPUBuilder.Optimizer.utils import *
from AIPUBuilder.Optimizer.framework import *


register_optype('DeQuantize')


@quant_register(OpType.DeQuantize)
def dequantize_quant(self, *args):
    inp = self.inputs[0]
    out = self.outputs[0]
    # unquantifiable op will not call quantize function


@op_register(OpType.DeQuantize)
def dequantize_forward(self, *args):
    inp = self.inputs[0]
    out = self.outputs[0]
    out.betensor = linear_dequantize(inp.betensor, inp.scale, inp.zerop)
    return out.betensor
