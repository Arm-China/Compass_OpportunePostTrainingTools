# SPDX-License-Identifier: Apache-2.0
# Copyright © 2022-2024 Arm Technology (China) Co. Ltd.

from AIPUBuilder.Optimizer.utils import *
from AIPUBuilder.Optimizer.framework import *
import AIPUBuilder.Optimizer.ops.activation as activation_module
import torch

# y = arccos x， x∈[–1，1]， y∈[0，π]


@quant_register(OpType.Acos)
def acos_quantize(self, *args):
    self.attrs['lambda_func'] = torch.acos
    self.attrs['out_signed'] = True
    activation_module.unknown_quantize(self, *args)
    for k in ['lambda_func', 'out_signed']:
        self.attrs.pop(k)


@op_register(OpType.Acos)
def acos(self, *args):
    def approximated_float_forward(self,  inp_tensor):
        if self.approximated and "lut" in self.constants:
            lut = self.constants["lut"].betensor
            out = lookup_float_index_lut(inp_tensor, lut,
                                         self.params['index_scale_value'],
                                         self.params['index_offset_value'],
                                         mirror_mode=True,
                                         value_offset_for_mirror_mode=self.params['value_offset_value'])
        else:
            out = torch.acos(inp_tensor)
        return out
    self.attrs['lambda_func'] = lambda x: approximated_float_forward(self,  x)
    self.outputs[0].betensor = activation_module.unknown_activation(self, *args)
    self.attrs.pop('lambda_func')
    return self.outputs[0].betensor


@approx_register(OpType.Acos)
def acos_approx(self, *args):
    def set_min_max(inp, use_dynamic_lut):
        if not use_dynamic_lut:
            clip_min = 0
            clip_max = 1
        else:
            clip_min = 0
            clip_max = max(abs(inp.min), abs(inp.max))
        return clip_min, clip_max

    self.attrs['set_min_max'] = set_min_max
    self.attrs['lambda_func'] = torch.acos
    self.attrs['out_signed'] = False
    self.attrs['value_offset'] = -torch.pi / 2
    activation_module.unknown_approx(self, *args)
    self.attrs.pop('lambda_func')
    self.attrs.pop('set_min_max')
    self.attrs.pop('out_signed')
    self.attrs.pop('value_offset')

    self.params['lut_mode'] = 'MIRROR'
