# SPDX-License-Identifier: Apache-2.0
# Copyright © 2023 Arm Technology (China) Co. Ltd.

from . parser import (arg_parser,
                      get_info_from_graph,
                      filter_valid_properties,
                      fields_to_str,
                      show_cfg_fields,
                      show_plugins)
from . cfg_fields import *


DEFAULT_CONFIG_FILE = 'opt_template.json'
