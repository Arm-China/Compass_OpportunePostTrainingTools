# SPDX-License-Identifier: Apache-2.0
# Copyright © 2022-2024 Arm Technology (China) Co. Ltd.

from AIPUBuilder.Optimizer.logger import *
from AIPUBuilder.Optimizer.framework import *
from . local_calibration import *
from . global_calibration import *
import torch
import re


def apply_calibration_strategy(t, strategy, quantize_method):
    if strategy:  # None means pass
        cstrategy = strategy.lower().strip()
        if cstrategy == 'extrema':
            extrema_calibration(t)
        elif cstrategy == 'in_ir':
            in_ir_calibration(t)
        elif cstrategy == 'mean':
            mean_calibration(t)
        elif re.match(r'^weighted_scale_param.*$', cstrategy):
            weighted_scale_param_calibration(t, cstrategy)
        elif re.match(r'^\d+std$', cstrategy):  # eg. 3std/5std/10std...
            nstd_calibration(t, cstrategy)
        elif re.match(r'^\d*kld$', cstrategy):  # eg. 5kld/10kld/20kld
            nkld_calibration(t, cstrategy)
        elif re.match(r'^(\d\.?\d*)*aciq_laplace$', cstrategy):
            aciq_laplace_calibration(t, cstrategy, quantize_method)
        elif re.match(r'^(\d\.?\d*)*aciq_gauss$', cstrategy):
            aciq_gauss_calibration(t, cstrategy, quantize_method)
        elif re.match(r'^(\d\.?\d*)*percentile$', cstrategy):
            percentile_calibration(t, cstrategy)
        else:
            OPT_WARN("unsupported calibration strategy: %s" % strategy)
            t.min = t.running_min
            t.max = t.running_max
            if None != t.running_min_key_axis:
                t.min_key_axis = t.running_min_key_axis
                t.max_key_axis = t.running_max_key_axis
    t.min = min(t.min, torch.tensor(0.0, device=t.device))
    t.max = max(t.max, torch.tensor(0.0, device=t.device))
    if None != t.min_key_axis:
        t.min_key_axis = torch.min(t.min_key_axis, torch.zeros_like(t.min_key_axis))
        t.max_key_axis = torch.max(t.max_key_axis, torch.zeros_like(t.max_key_axis))


def apply_global_calibration(g, cdataloader, strategy):
    methods = strategy
    OPT_INFO('applying global calibration strategy: ')
    for method in methods:
        mname = method[0]
        mparams = method[1]
        mscopes = method[2]
        if 'easy_quant' == mname:
            easy_quant_global_calibration(g, cdataloader, mparams, mscopes)
        elif 'adaround' == mname:
            adaround_global_calibration(g, cdataloader, mparams, mscopes)
        elif 'adaquant_zy' == mname:
            adaquant_zy_global_calibration(g, cdataloader, mparams, mscopes)
        elif 'gptq_zy' == mname:
            gptq_zy_global_calibration(g, cdataloader, mparams, mscopes)
        elif 'smooth_quant_zy' == mname:
            smooth_quant_zy_global_calibration(g, cdataloader, mparams, mscopes)
        elif 'awq_zy' == mname:
            awq_zy_global_calibration(g, cdataloader, mparams, mscopes)
        elif 'svd_quant' == mname:
            svd_based_quant_global_calibration(g, cdataloader, mparams, mscopes)
        elif 'mvn_correction' == mname:
            mvn_correction_global_calibration(g, cdataloader, mparams, mscopes)
        else:
            pass


def statistic_and_calibration(t: PyTensor, node_attrs: dict, is_constant_tensor: bool):
    from AIPUBuilder.Optimizer.config import CalibrationStrategyField
    dv = ((float('-inf'), float('inf')), '')
    tcmd = [x for x in re.split(
        r',|\(|\)', node_attrs['trim_infinity_before_statistic'].strip()) if x.lower().strip()]
    trim_inf = dv if len(tcmd) < 3 else ((float(tcmd[1]), float(tcmd[2])), str(tcmd[0]))

    qstrategy = node_attrs['q_strategy_weight'] if is_constant_tensor else node_attrs['q_strategy_activation']
    qmethod = node_attrs['q_mode_weight'] if is_constant_tensor else node_attrs['q_mode_activation']
    r = CalibrationStrategyField._need_statistic_info(qstrategy)
    histc_bins = None if not r['histc'] else node_attrs["histc_bins"]
    statistic_std_mean = r['std_mean']
    t.statistic(running_statistic_momentum=1.0, key_axis=t.key_axis, key_axis_g=t.key_axis_g,
                histc_bins=histc_bins, statistic_std_mean=statistic_std_mean,
                trim_infinity=trim_inf,
                reset=True)
    apply_calibration_strategy(t, qstrategy, qmethod)
