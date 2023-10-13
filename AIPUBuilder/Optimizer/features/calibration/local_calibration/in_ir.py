# SPDX-License-Identifier: Apache-2.0
# Copyright © 2023 Arm Technology (China) Co. Ltd.


def in_ir_calibration(t, *args):
    t.min = t.extrema_min
    t.max = t.extrema_max
    if t.extrema_min_key_axis is not None:
        t.min_key_axis = t.extrema_min_key_axis
        t.max_key_axis = t.extrema_max_key_axis
