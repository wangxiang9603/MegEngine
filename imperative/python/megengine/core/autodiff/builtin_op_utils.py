# -*- coding: utf-8 -*-
# MegEngine is Licensed under the Apache License, Version 2.0 (the "License")
#
# Copyright (c) 2014-2020 Megvii Inc. All rights reserved.
#
# Unless required by applicable law or agreed to in writing,
# software distributed under the License is distributed on an
# "AS IS" BASIS, WITHOUT ARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
import functools
import itertools

import numpy as np

from .._imperative_rt import TensorAttr, imperative
from .._imperative_rt.core2 import apply
from ..ops.builtin import (
    Broadcast,
    Elemwise,
    GetVarShape,
    IndexingMultiAxisVec,
    IndexingSetMultiAxisVec,
    OpDef,
    Reduce,
    Reshape,
    SetSubtensor,
    Subtensor,
)
from ..ops.special import Const


def default_grad_fn(op, inputs, outputs, input_requires_grad):
    def get_tensor_attr(x):
        attr = TensorAttr()
        attr.dtype = x.dtype
        attr.comp_node = x.device.to_c()
        return attr

    output_has_grads = [True,] * len(outputs)
    result = imperative.make_backward_graph(
        op, list(map(get_tensor_attr, inputs)), input_requires_grad, output_has_grads
    )
    if result is None:
        nr_inputs = len(inputs)
        nr_outputs = len(outputs)

        def backward(*args):
            return nr_inputs * [
                None,
            ]

        return backward, nr_outputs * [False,]
    backward_graph, save_for_backward_mask, input_has_grad = result

    intput_output_mask = save_for_backward_mask[: len(inputs + outputs) :]
    output_grad_mask = save_for_backward_mask[len(inputs + outputs) :]
    save_for_backward = tuple(
        val for val, mask in zip(inputs + outputs, intput_output_mask) if mask
    )

    del inputs
    del outputs

    def backward(*args):
        output_grads = tuple(val for val, mask in zip(args, output_grad_mask) if mask)
        assert None not in output_grads
        ret = iter(apply(backward_graph, *(save_for_backward + output_grads)))
        return tuple(next(ret) if mask else None for mask in input_has_grad)

    return backward, output_grad_mask


def get_shape(x):
    (s,) = apply(GetVarShape(), x._data)
    return Tensor(s)


# override for Elemwise.add
def elemwise_add_grad_fn(op, inputs, outputs, input_requires_grad):
    assert len(inputs) == len(input_requires_grad) == 2

    input_shapes = [
        get_shape(x) if i else None for i, x in zip(input_requires_grad, inputs)
    ]

    def reduce_to(x, s):
        (y,) = apply(Reduce(), x, s)
        return y

    def backward(dy):
        return tuple(
            reduce_to(dy, s) if i else None
            for i, s in zip(input_requires_grad, input_shapes)
        )

    return backward, [True]


# override for Reshape
def reshape_grad_fn(op, inputs, outputs, input_requires_grad):
    assert len(inputs) == len(input_requires_grad) == 2

    input_shapes = [
        get_shape(x) if i else None for i, x in zip(input_requires_grad, inputs)
    ]

    def reshape_to(dy, s):
        (dx,) = apply(Reshape(), dy, s)
        return dx

    def backward(dy):
        return tuple(
            reshape_to(dy, s) if i else None
            for i, s in zip(input_requires_grad, input_shapes)
        )

    return backward, [True]


# override for Subtensor
def subtensor_grad_fn(op, inputs, outputs, input_requires_grad):
    grad_op = SetSubtensor(op.items)

    input_shape = get_shape(inputs[0])
    params = inputs[1:]

    def make_grad(grad_op, dy):
        (_z,) = Const(0, dtype=dy.dtype, device=dy.device)(dy)
        (grad,) = apply(Broadcast(), _z, input_shape)
        (dx,) = apply(grad_op, grad, dy, *params)
        return dx

    def backward(dy):
        return tuple(
            make_grad(grad_op, dy) if mask else None for mask in input_requires_grad
        )

    return backward, [True]


# override for IndexingMultiAxisVec
def indexingMultiAxisVec_grad_fn(op, inputs, outputs, input_requires_grad):
    grad_op = IndexingSetMultiAxisVec(op.items)

    input_shape = get_shape(inputs[0])
    params = inputs[1:]

    def make_grad(grad_op, dy):
        (_z,) = Const(0, dtype=dy.dtype, device=dy.device)(dy)
        (grad,) = apply(Broadcast(), _z, input_shape)
        (dx,) = apply(grad_op, grad, dy, *params)
        return dx

    def backward(dy):
        return tuple(
            make_grad(grad_op, dy) if mask else None for mask in input_requires_grad
        )

    return backward, [True]


# override for Reduce.sum
def reduce_sum_grad_fn(op, inputs, outputs, input_requires_grad):
    assert len(inputs) == len(input_requires_grad) == 1
    input_shape = get_shape(inputs[0])

    def broadcast_to(dy, s):
        (dx,) = apply(Broadcast(), dy, s)
        return dx

    def backward(dy):
        return (broadcast_to(dy, input_shape) if input_requires_grad[0] else None,)

    return backward, [True]
