# SPDX-License-Identifier: Apache-2.0
# Copyright © 2022-2024 Arm Technology (China) Co. Ltd.

#!/usr/bin/python3
# -*- coding: UTF-8 -*-

__OPT_VERSION__ = '1.3'
__build_number__ = None     # placeholder for build script
if __build_number__ is not None:
    __OPT_VERSION__ = __OPT_VERSION__+"."+str(__build_number__)
__OPT_NAME__ = 'Compass-Optimizer'
