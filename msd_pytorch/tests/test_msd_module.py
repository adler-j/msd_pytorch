from . import torch_equal
from pytest import approx
from msd_pytorch.conv import Conv2dInPlaceModule
from msd_pytorch.msd_module import MSDModule, MSDFinalLayer
from msd_pytorch.relu_inplace import ReLUInplaceFunction
from msd_pytorch.stitch import stitchCopy
from torch.autograd import Variable
import msd_pytorch.stitch as stitch
import torch as t
import torch.nn as nn
import torch.optim as optim
import unittest
from torch.autograd import gradcheck
from torch.autograd.gradcheck import get_analytical_jacobian


def test_msd_gradients():
    t.manual_seed(1)

    dtype = t.double
    size = (11, 13)
    batch_sz = 2

    for depth in [9]:
        print(f"Depth: {depth}")
        width = c_in = c_out = batch_sz
        x = Variable(t.randn(batch_sz, c_in, *size, dtype=dtype)).cuda()
        x.requires_grad = True

        net = MSDModule(c_in, c_out, depth, width)
        net.double()

        for p in net.parameters():
            p.data = t.randn_like(p.data)

        assert net is not None

        # o = net(x)
        # analytical, reentrant, correct_grad_sizes = get_analytical_jacobian((x,), o)
        # print(analytical)
        # print(f"Reentrant: {reentrant}")
        # print(correct_grad_sizes)
        # print(f"Net L shape: {net.L.shape}")
        gradcheck(net, [x], raise_exception=True)


def test_final_layer():
    """Test MSDFinalLayer module

    We check that the msd_final module does exactly the same as a
    1x1 convolution.

    """
    for conv3d in [False, True]:

        shape = (25,) * (3 if conv3d else 2)
        k_shape = (1,) * (3 if conv3d else 2)
        device = t.device("cuda:0")
        dtype = t.double

        batch_size = 3
        c_in = 10
        c_out = 2

        input = t.randn(batch_size, c_in, *shape, dtype=dtype, device=device)
        bias = t.randn(c_out, dtype=dtype, device=device)
        weight = t.randn(c_out, c_in, 1, dtype=dtype, device=device)

        msd_final = MSDFinalLayer(c_in, c_out)
        msd_final.linear.bias.data = bias
        msd_final.linear.weight.data = weight

        if conv3d:
            conv = nn.Conv3d(c_in, c_out, 1)
        else:
            conv = nn.Conv2d(c_in, c_out, 1)
        conv.bias.data = bias
        conv.weight.data = weight.view(c_out, c_in, *k_shape)

        # Check that outputs have the same shape
        output1 = conv(input)
        output2 = msd_final(input)
        assert output1.shape == output2.shape

        # And have the same values.
        diff = (output1 - output2).abs().sum().item()
        assert diff == approx(0)


def test_reflect():
    batch_sz = 1
    c_in, c_out = 2, 3
    depth, width = 11, 3
    size = (20,) * 2
    x = t.randn(batch_sz, c_in, *size).cuda()
    target = t.randn(batch_sz, c_out, *size).cuda()

    net = MSDModule(c_in, c_out, depth, width)

    output = net(Variable(x))

    assert target.shape == output.shape
    assert output.data.abs().sum().item() == approx(0)


def test_with_tail():
    batch_sz = 1
    c_in, c_out = 2, 3
    depth, width = 11, 3
    size = (20,) * 2
    x = t.randn(batch_sz, c_in, *size).cuda()
    target = Variable(t.randn(batch_sz, 1, *size).cuda())

    net = nn.Sequential(MSDModule(c_in, c_out, depth, width), nn.Conv2d(3, 1, 1))
    net.cuda()

    output = net(Variable(x))
    loss = nn.MSELoss()(output, target)
    loss.backward()

    assert output.abs().sum().item() != approx(0.0)


def test_backward():
    dbg = False

    def dbg_print(*args):
        if dbg:
            print(*args)

    d = 3
    input_size = (20, 20)
    # Prepare layer and gradient storage
    L = t.zeros(1, d + 1, *input_size).cuda()
    G = t.zeros(1, d + 1, *input_size).cuda()

    cs = [
        Conv2dInPlaceModule(None, i + 1, 1, kernel_size=3, dilation=1) for i in range(d)
    ]
    relu = nn.ReLU(inplace=True)
    relu = ReLUInplaceFunction.apply

    for i, c in enumerate(cs):
        c.weight.data.fill_(i + 1)
        c.bias.data.fill_(i % 2)

    x = t.Tensor(1, 1, *input_size).fill_(2).cuda()
    x.requires_grad = True

    dbg_print("A", L._version)
    output = stitchCopy(x, L, G, 0)
    dbg_print("B", L._version, output._version, output.grad_fn)

    conv = cs[0]
    conv.output = L.narrow(1, 1, 1)  # narrow(L, 1, 1, 1)
    dbg_print("C", L._version, output._version, output.grad_fn)
    output = conv(x)
    dbg_print("D", L._version, output._version, output.grad_fn)
    output = relu(output)
    dbg_print("E", L._version, output._version, output.grad_fn)
    output = stitch.stitchLazy(output, L, G, 1)
    dbg_print("F", L._version, output._version, output.grad_fn)

    conv = cs[1]
    conv.output = L.narrow(1, 2, 1)  # narrow(L, 1, 2, 1)
    dbg_print("G", L._version, output._version, output.grad_fn)
    output = conv(output)
    dbg_print("H", L._version, output._version, output.grad_fn)
    output = relu(output)
    dbg_print("I", L._version, output._version, output.grad_fn)
    output = stitch.stitchLazy(output, L, G, 2)
    dbg_print("J", L._version, output._version, output.grad_fn)

    conv = cs[2]
    dbg_print("K", L._version, output._version, output.grad_fn)
    conv.output = L.narrow(1, 3, 1)  #
    dbg_print("L", L._version, output._version, output.grad_fn)
    output = conv(output)
    dbg_print("M", L._version, output._version, output.grad_fn)
    output = relu(output)
    dbg_print("N", L._version, output._version, output.grad_fn)
    output = stitch.stitchLazy(output, L, G, 3)
    dbg_print("O", L._version, output._version, output.grad_fn)

    output.backward(t.ones_like(output))
    dbg_print(x.grad.shape)


def test_parameters_change():
    # This test ensures that all parameters are updated after an
    # update step.
    t.manual_seed(1)

    size = (30, 30)
    for batch_sz in [1]:
        for depth in range(0, 20, 6):
            width = c_in = c_out = batch_sz
            x = Variable(t.randn(batch_sz, c_in, *size)).cuda()
            target = Variable(t.randn(batch_sz, c_out, *size)).cuda()
            assert x.data.is_cuda

            net = MSDModule(c_in, c_out, depth, width)

            assert net is not None

            params0 = dict((n, p.data.clone()) for n, p in net.named_parameters())
            # Train for two iterations. The convolution weights in
            # the MSD layers are not updated after the first
            # training step because the final 1x1 convolution
            # weights are zero.
            optimizer = optim.Adam(net.parameters())
            optimizer.zero_grad()
            for _ in range(2):
                y = net(x)
                assert y is not None
                criterion = nn.L1Loss()
                loss = criterion(y, target)
                loss.backward()
                optimizer.step()

            params1 = dict(net.named_parameters())

            for name in params1.keys():
                p0, p1 = params0[name], params1[name]
                d = abs(p0 - p1.data.clone()).sum().item()
                assert 0.0 < d, (
                    f"Parameter {name} left unchanged: \n"
                    f"Initial value: {p0}\n"
                    f"Current value: {p1}\n"
                    f"Gradient: {p1.grad}\n"
                )

            # Check that the loss is not zero
            assert loss.abs().item() != approx(0.0)
