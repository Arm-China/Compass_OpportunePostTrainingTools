# Copyright © 2023 Arm Technology (China) Co. Ltd. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

from AIPUBuilder.Optimizer.utils import *
from AIPUBuilder.Optimizer.framework import *
import torch


@quant_register(OpType.Moments)
def moments_quantize(self, *args):
    q_mode_activation = self.attrs["q_mode_activation"]
    if QuantMode.is_per_channel(q_mode_activation) == True:
        OPT_FATAL("Currently not support per-channel quantization of activations")
    q_bits_activation = self.attrs["q_bits_activation"]
    inp = self.inputs[0]
    out0 = self.outputs[0]
    out1 = self.outputs[1]

    out0.qbits = inp.qbits
    out0.dtype = inp.dtype
    out0.scale = inp.scale
    out0.zerop = inp.zerop
    out0.qmin = inp.qmin
    out0.qmax = inp.qmax
    out0.qinvariant = inp.qinvariant

    if inp.qinvariant:
        out1.qbits = inp.qbits * 2
        out1.dtype = bits2dtype(out1.qbits, False or self.force_dtype_int)
        out1.scale = 1.0
        out1.zerop = 0
        out1.qmin, out1.qmax = dtype2range(out1.dtype)
        out1.qinvariant = True

        self.params['var_scale_value'] = 1
        self.params['var_scale_type'] = bits2dtype(q_bits_activation, False)
        self.params['var_shift_value'] = 0
        self.params['var_shift_type'] = Dtype.INT8
    else:
        out1.qinvariant = False
        out1.qbits = q_bits_activation
        out1_signed = False or self.force_dtype_int
        out1.scale, out1.zerop, out1.qmin, out1.qmax, out1.dtype = get_linear_quant_params_from_tensor(out1,
                                                                                                       q_mode_activation,
                                                                                                       q_bits_activation,
                                                                                                       out1_signed)
        do_scale, do_scale_type, do_shift, do_shift_type = \
            get_scale_approximation_params(out1.scale / (inp.scale * inp.scale),
                                           mult_bits=q_bits_activation,
                                           force_shift_positive=self.force_shift_positive)

        self.params['var_scale_value'] = int(do_scale)
        self.params['var_scale_type'] = do_scale_type
        self.params['var_shift_value'] = int(do_shift)
        self.params['var_shift_type'] = do_shift_type

    # Retain this parameter for now and adjust it later
    self.params['input_scale_value'] = 1
    self.params['input_scale_type'] = bits2dtype(q_bits_activation, False)
    self.params['input_shift_value'] = 0
    self.params['input_shift_type'] = Dtype.INT8


@op_register(OpType.Moments)
def moments_forward(self, *args):
    inp = self.inputs[0]
    out0 = self.outputs[0]
    out1 = self.outputs[1]
    axis = self.get_param('axis')
    keep_dim = self.get_param('keepdims', optional=True, default_value=True)
    if self.quantized:
        input_scale = self.get_param('input_scale_value')
        input_shift = self.get_param('input_shift_value')
        do_scale = self.get_param('var_scale_value')
        do_shift = self.get_param('var_shift_value')
        qmin, qmax = dtype2range(inp.dtype)
        input_tensor = linear_requantize(inp.betensor, input_scale, input_shift, 0, qmin, qmax)
        t_mean = torch.mean(input_tensor.double(), dim=axis, keepdim=True).round().long()
        t_var = torch.mean((input_tensor.double() - t_mean) ** 2, dim=axis, keepdim=True).long()
        if not keep_dim:
            new_shape = []
            dim_num = input_tensor.dim()
            axis = [ax+dim_num if ax < 0 else ax for ax in axis]
            for ax in range(dim_num):
                if ax not in axis:
                    new_shape.append(input_tensor.shape[ax])
            t_mean = torch.reshape(t_mean, new_shape)
            t_var = torch.reshape(t_var, new_shape)
        out0.betensor = t_mean
        out1.betensor = linear_requantize(t_var, do_scale, do_shift, out1.zerop, out1.qmin, out1.qmax)
    else:
        t_std, t_mean = torch.std_mean(inp.betensor, dim=axis, keepdim=keep_dim, unbiased=False)
        out0.betensor = t_mean
        out1.betensor = t_std * t_std

    return out0.betensor, out1.betensor
