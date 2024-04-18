# SPDX-License-Identifier: Apache-2.0
# Copyright © 2022-2024 Arm Technology (China) Co. Ltd.

from AIPUBuilder.Optimizer.framework import *
from AIPUBuilder.Optimizer.utils import *


@passes_run
def merge_matmul_mul(graph, config=None):
    # find matmul+scale(represented by operator: bn or mul) and fuse related nodes into Matmul (with additional param: fused_multiplier)
    end_idx = len(graph.nodes)
    node_idx = 0
    ncount = end_idx
    wcount1 = 0
    while node_idx < end_idx and wcount1 < ncount:
        n = graph.nodes[node_idx]
        wcount1 += 1
        if OpType.MatMul == n.type and 1 == len(n.children):
            snodes = []
            flag = False
            fused_multiplier = 1.0
            n1 = n
            n2 = n.children[0]
            wcount2 = 0
            while wcount2 < ncount:
                wcount2 += 1
                if n2.type in OP_ONLY_CHANGE_SHAPE and 1 == len(n2.children):
                    n1 = n2
                    n2 = n2.children[0]
                elif OpType.BatchNorm == n2.type:
                    w = n2.constants['weights'].betensor.unique()
                    b = n2.constants['biases'].betensor.unique()
                    if 1 == w.numel() and 1 == b.numel() and 0. == b.item() and w.item() > 0.:
                        flag = True
                        fused_multiplier = w
                        snodes.append(n2)
                    break
                elif (OpType.Mul == n2.type) or (OpType.Eltwise == n2.type and n2.get_param('method').lower() == 'mul') and n2.get_param('with_activation').lower() == 'none':
                    scale_nodes, count_root, count_constant = (
                        n2.parents[0] if n2.parents[0] != n1 else n2.parents[1]).get_ancestors()
                    if count_root > 0 and count_root == count_constant:
                        gview = graph.subgraph_view(scale_nodes)
                        if len(gview.outflow_tensors) == 1 and gview.outflow_tensors[0] in n2.inputs:
                            for node in scale_nodes:
                                node.forward()
                            s = gview.outflow_tensors[0].betensor.unique()
                            if 1 == s.numel() and s.item() > 0. and n2.outputs[0].ir_shape.size() == (n2.inputs[0] if n2.inputs[1].pnode in scale_nodes else n2.inputs[1]).ir_shape.size():
                                flag = True
                                fused_multiplier = s
                                snodes.extend(gview.nodes)
                                snodes.append(n2)
                    break
                else:
                    break
            for sn in snodes:
                for st in (sn.inputs + sn.outputs):
                    if st in (graph.input_tensors + graph.output_tensors):
                        flag = False
                        break
            if flag:
                rn = snodes[-1]
                OPT_DEBUG(f"These nodes will be cut out during pass merge_matmul_mul_s1: {snodes[:-1]}")
                graph.cut_subgraph(snodes[:-1])
                # n.replace_output_temporarily(0, ot)
                if len(rn.inputs) > 1:
                    t = rn.inputs[0] if rn.inputs[0].pnode in snodes else rn.inputs[1]
                    rn.remove_input(t)
                rn.type = OpType.Crop
                rn.params.clear()
                rn.params['crops'] = [[0, i] for i in rn.outputs[0].ir_shape]
                rn.constants.clear()
                rn.placeholders.clear()
                graph.init_networkx()
                n.params['fused_multiplier'] = fused_multiplier
                n.attrs['fused_multiplier'] = fused_multiplier
                node_idx = graph.nodes.index(n)
        node_idx += 1
        end_idx = len(graph.nodes)
    if ncount - len(graph.nodes) > 0:
        OPT_INFO(f"{ncount - len(graph.nodes)} nodes were cut during pass merge_matmul_mul_s1")
