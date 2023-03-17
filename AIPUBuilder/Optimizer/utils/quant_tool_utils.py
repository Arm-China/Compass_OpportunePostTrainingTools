# Copyright © 2023 Arm Technology (China) Co. Ltd. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import torch
from collections import namedtuple
from collections import defaultdict
from AIPUBuilder.Optimizer.framework import *
from AIPUBuilder.Optimizer.logger import *
from AIPUBuilder.Optimizer.utils.dtype_utils import *

SHIFT_DTYPE = Dtype.INT8
OP_NEED_ALIGN_INP_OUT_DTYPE = [OpType.GRUv3, OpType.GRUv1, OpType.BasicLSTM]
OP_NEED_ADD_PAD_AVOID_ASYNC_DIVIDE = [OpType.Pooling, OpType.Pooling3D]
ASYM2SYM_OP_DICT = {  # (op.type)      : (tensor_idx,         QuantMode)
    OpType.BasicLSTM:  ((1, 2),    "per_tensor_symmetric_restricted_range"),
    OpType.GRUv1:      ((1,),       "per_tensor_symmetric_restricted_range"),
    OpType.GRUv3:      ((1,),       "per_tensor_symmetric_restricted_range"),
}

#########################################################################################################################
# class routines


class QuantMode:
    Mode = namedtuple('QuantMode', ['name', 'is_per_channel', 'is_asymmetric', 'is_full_range'])
    modes = [
        # name                                        is_per_channel      is_asymmetric       is_full_range
        Mode("per_tensor_symmetric_restricted_range",    False,              False,              False),
        Mode("per_tensor_symmetric_full_range",          False,              False,              True),
        Mode("per_tensor_asymmetric",                    False,              True,               True),
        Mode("per_channel_symmetric_restricted_range",   True,               False,              False),
        Mode("per_channel_symmetric_full_range",         True,               False,              True),
        Mode("per_channel_asymmetric",                   True,               True,               True),
    ]
    name_list_ = [mode.name for mode in modes]
    is_valid_ = defaultdict(lambda: False,  {mode.name: True for mode in modes})
    is_per_channel_ = {mode.name: mode.is_per_channel for mode in modes}
    is_asymmetric_ = {mode.name: mode.is_asymmetric for mode in modes}
    is_full_range_ = {mode.name: mode.is_full_range for mode in modes}

    @classmethod
    def mode_names(cls):
        return cls.name_list_

    @classmethod
    def default_mode(cls):
        return cls.name_list_[0]

    @classmethod
    def is_valid(cls, mode_name):
        return cls.is_valid_[mode_name.lower()]

    @classmethod
    def is_per_channel(cls, mode_name):
        return cls.is_per_channel_[mode_name.lower()]

    @classmethod
    def is_per_tensor(cls, mode_name):
        return not cls.is_per_channel_[mode_name.lower()]

    @classmethod
    def is_asymmetric(cls, mode_name):
        return cls.is_asymmetric_[mode_name.lower()]

    @classmethod
    def is_symmetric(cls, mode_name):
        return not cls.is_asymmetric_[mode_name.lower()]

    @classmethod
    def is_full_range(cls, mode_name):
        return cls.is_full_range_[mode_name.lower()]

    @classmethod
    def make_mode(cls, is_per_channel, is_asymmetric, is_full_range):
        m = ''
        if is_per_channel:
            m += 'per_channel'
        else:
            m += 'per_tensor'
        if is_asymmetric:
            m += '_asymmetric'
        else:
            m += '_symmetric'
            if is_full_range:
                m += '_full_range'
            else:
                m += '_restricted_range'
        return m

    @classmethod
    def to_per_channel(cls, mode_name):
        return cls.make_mode(True, cls.is_asymmetric(mode_name), cls.is_full_range(mode_name))

    @classmethod
    def to_per_tensor(cls, mode_name):
        return cls.make_mode(False, cls.is_asymmetric(mode_name), cls.is_full_range(mode_name))

    @classmethod
    def to_symmetric(cls, mode_name):
        return cls.make_mode(cls.is_per_channel(mode_name), False, False)

    @classmethod
    def to_asymmetric(cls, mode_name):
        return cls.make_mode(cls.is_per_channel(mode_name), True, cls.is_full_range(mode_name))

    @classmethod
    def to_full_range(cls, mode_name):
        return cls.make_mode(cls.is_per_channel(mode_name), cls.is_asymmetric(mode_name), True)

    @classmethod
    def to_restricted_range(cls, mode_name):
        return cls.make_mode(cls.is_per_channel(mode_name), cls.is_asymmetric(mode_name), False)

#########################################################################################################################
# math routines


def cosine_distance(a, b):
    x = torch.tensor(a, dtype=torch.float64) if not isinstance(a, torch.Tensor) else a.double()
    y = torch.tensor(b, dtype=torch.float64) if not isinstance(b, torch.Tensor) else b.double()

    t1 = x.flatten()
    t2 = y.flatten()
    t1_m = torch.norm(t1, p=2)
    t2_m = torch.norm(t2, p=2)
    t1t2 = torch.dot(t1, t2)
    t1t2_m = t1_m * t2_m
    if t1t2_m.item() == 0.0:
        if t1_m == t2_m:
            return 1.0
        else:
            return 0.0
    else:
        if t1t2 == t1t2_m:
            return 1.0
        else:
            return (t1t2 / t1t2_m).item()


def layer_similarity(n, qn):
    t1_list = []
    for t in n.outputs:
        t1_list.append(t.betensor.reshape(-1).float())
    t2_list = []
    for t in qn.outputs:
        t2_list.append(t.betensor.reshape(-1).float())
    t1 = torch.cat(t1_list)
    t2 = torch.cat(t2_list)
    return cosine_distance(t1, t2)
############################################
# x_q = round(scale * x_f - zerop)
# x_f = (x_q + zerop) / scale
# where scale = q_range / f_range, zerop = f_min * scale - q_min
# refers to https://intellabs.github.io/distiller/algo_quantization.html
#
# note that we don't force f_min <= 0 or f_max >= 0,
# so zerop may exceed bits range and need to be represented by accum_bits,
# and we just assume that this is assure by apply_calibration_strategy.
############################################


def get_linear_quant_params_from_tensor(x, quant_mode, bits, is_signed):
    assert QuantMode.is_valid(quant_mode), "%s is not one of '%s'" % (quant_mode, str(QuantMode.mode_names()))
    assert bits > 0
    QUANTIZE_ZERO_BAND = torch.finfo(torch.float32).eps

    q_max, q_min = 2 ** bits - 1, 0
    if is_signed:
        if QuantMode.is_full_range(quant_mode):
            q_max = 2 ** (bits - 1) - 1
            q_min = -1 * q_max - 1
        else:
            q_max = 2 ** (bits - 1) - 1
            q_min = -1 * q_max
    q_range = q_max - q_min

    f_ranges = torch.tensor(x.max - x.min, dtype=torch.float32, device=x.betensor.device)
    f_zfactor = torch.tensor(x.min, dtype=torch.float32, device=x.betensor.device)
    f_zoffset = torch.zeros_like(f_zfactor)
    if QuantMode.is_per_channel(quant_mode):
        if QuantMode.is_asymmetric(quant_mode):
            f_ranges = x.max_key_axis - x.min_key_axis
            f_zfactor = x.min_key_axis
            f_zoffset = q_min * torch.ones_like(f_zfactor)
        else:
            f_ranges = torch.max(x.max_key_axis.abs(), x.min_key_axis.abs())
            if is_signed:
                f_ranges = f_ranges * 2.0
            f_zfactor = torch.zeros_like(x.min_key_axis)
            f_zoffset = torch.zeros_like(f_zfactor)
    else:
        if QuantMode.is_asymmetric(quant_mode):
            f_ranges = torch.tensor(x.max - x.min, dtype=torch.float32, device=x.betensor.device)
            f_zfactor = torch.tensor(x.min, dtype=torch.float32, device=x.betensor.device)
            f_zoffset = q_min * torch.ones_like(f_zfactor)
        else:
            f_ranges = torch.tensor(max(abs(x.max), abs(x.min)), dtype=torch.float32, device=x.betensor.device)
            if is_signed:
                f_ranges = f_ranges * 2.0
            f_zfactor = torch.tensor(0.0, dtype=torch.float32, device=x.betensor.device)
            f_zoffset = torch.zeros_like(f_zfactor)
    q_ranges = q_range * torch.ones_like(f_ranges)
    f_ranges = torch.where(f_ranges < QUANTIZE_ZERO_BAND, q_ranges, f_ranges)
    f_ranges = torch.where(torch.isnan(f_ranges), q_ranges, f_ranges)
    scale = q_ranges / f_ranges
    zerop = torch.clamp(scale.mul(f_zfactor).round() - f_zoffset, -2 ** (bits - 1) + 1, 2 ** (bits - 1))

    if scale.dim() < 1:
        return scale.item(), zerop.item(), q_min, q_max, bits2dtype(bits, is_signed)
    else:
        return scale, zerop, q_min, q_max, bits2dtype(bits, is_signed)


def linear_quantize_clip(x, scale, zero_point, clamp_min, clamp_max):
    x_t = x if isinstance(x, torch.Tensor) else torch.tensor(x)
    x_type = x_t.dtype
    y = torch.clamp(torch.round(scale * x_t.float() - zero_point), clamp_min, clamp_max).float()
    y = torch.where(torch.isnan(y), torch.zeros_like(y, device=y.device), y)
    xmin, xmax = dtype2range(torch_type2dtype(x_type))
    if (xmin <= clamp_min) and (xmax >= clamp_max):
        return y.to(x_type)
    else:
        return y


def linear_dequantize(x, scale, zero_point):
    return (x + zero_point) / (scale.float() if isinstance(scale, torch.Tensor) else float(scale))


def get_scale_approximation_params(fp32_scale_value, mult_bits, limit=False, mult_bits_ceil=15, shift_bits_ceil=31, force_shift_positive=False):
    fp32_scale = fp32_scale_value if isinstance(
        fp32_scale_value, torch.Tensor) else torch.tensor(fp32_scale_value, dtype=torch.float32)
    mbits = mult_bits if isinstance(mult_bits, torch.Tensor) else torch.tensor(mult_bits)
    # AIFF max support multiplier value is 32767, so we limit mbits to less equal than 15
    mbits = torch.minimum(mult_bits_ceil*torch.ones_like(mbits), mbits)
    shift_bits = torch.log2((2.0 ** mbits - 1.0) / fp32_scale).floor()
    if limit:
        shift_bits = torch.minimum(mbits, shift_bits)
    # AIFF max support shift value is 31, so we limit shift to less equal than 31
    shift_bits = torch.minimum(shift_bits, torch.ones_like(shift_bits) * shift_bits_ceil)
    multiplier = (fp32_scale * (2.0 ** shift_bits) + 0.5).floor()
    if force_shift_positive:
        shift_less0_mask = shift_bits < 0
        shift_bits[shift_less0_mask] = 0
        multiplier[shift_less0_mask] = min(2.0**mult_bits-1.0, 2.0**mult_bits_ceil-1.0)
    q_bits = 8 if mult_bits <= 8 else 16
    multiplier_type = bits2dtype(q_bits, is_signed=False)
    _, shiftbits_type = range2dtype(shift_bits.min(), shift_bits.max(), force_int=True)
    if (multiplier - 2.0**shift_bits).abs().max().item() < OPT_EPSILON:
        multiplier = torch.ones_like(multiplier)
        shift_bits = torch.zeros_like(shift_bits)
    if fp32_scale.dim() < 1:
        return multiplier.item(), multiplier_type, shift_bits.item(), shiftbits_type
    else:
        return multiplier, multiplier_type, shift_bits, shiftbits_type


def linear_requantize(x, multiplier, shift_bits, zero_point, clamp_min, clamp_max):
    x_t = x if isinstance(x, torch.Tensor) else torch.tensor(x)
    x_type = x_t.dtype
    y = torch.clamp(torch.round(x_t.float() * multiplier * (0.5 ** shift_bits)) -
                    zero_point, clamp_min, clamp_max).float()
    y = torch.where(torch.isnan(y), torch.zeros_like(y, device=y.device), y)
    xmin, xmax = dtype2range(torch_type2dtype(x_type))
    if (xmin <= clamp_min) and (xmax >= clamp_max):
        return y.to(x_type)
    else:
        return y


def linear_requantize_floor(x, multiplier, shift_bits, zero_point, clamp_min, clamp_max):
    x_t = x if isinstance(x, torch.Tensor) else torch.tensor(x)
    x_type = x_t.dtype
    shift_bits = shift_bits.to(torch.long) if isinstance(
        shift_bits, torch.Tensor) else torch.tensor(shift_bits, dtype=torch.long, device=x_t.device)
    y_tmp = torch.round(x_t.float() * multiplier).to(torch.long)
    y_tmp = torch.where(shift_bits >= 0, y_tmp >> shift_bits, y_tmp << torch.abs(shift_bits))
    y = torch.clamp(y_tmp - zero_point, clamp_min, clamp_max).float()
    y = torch.where(torch.isnan(y), torch.zeros_like(y, device=y.device), y)
    xmin, xmax = dtype2range(torch_type2dtype(x_type))
    if (xmin <= clamp_min) and (xmax >= clamp_max):
        return y.to(x_type)
    else:
        return y
