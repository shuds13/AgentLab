"""A 2D wind tunnel: lattice Boltzmann (D2Q9, BGK) in PyTorch, on a GPU where there is one.

    python sim.py args.json

args.json: {"radii": [...], "resolution": D, "reynolds": Re, "save_field": bool,
"out_dir": path}. Prints one JSON line with the result and writes flow.png (and
field.npz with save_field) to out_dir.

Lengths are in lattice cells. D is the diameter of the circle with the shape's area, the
unit everything is measured in. The tunnel is 16 D long and 6 D high, the shape's centre
4 D from the inlet; the top and bottom wrap around (periodic). Air enters on the left at
U = 0.1 cells per step and leaves on the right. The viscosity follows from the Reynolds
number, U D / nu. Drag is the force on the shape from the populations that bounce off
it each step (momentum exchange), averaged over the last third of the run.
"""
import json
import math
import os
import struct
import sys
import time
import zlib

import numpy as np
import torch

U = 0.1                               # inflow speed, cells per step
LENGTH, HEIGHT, X0 = 16, 6, 4         # tunnel, and shape centre, in D
STEPS_PER_D = 600                     # run length: 600 D steps, about 3.75 flow-throughs

CX = [0, 1, 0, -1, 0, 1, -1, -1, 1]
CY = [0, 0, 1, 0, -1, 1, 1, -1, -1]
W = [4 / 9] + [1 / 9] * 4 + [1 / 36] * 4
OPP = [0, 3, 4, 1, 2, 7, 8, 5, 6]


def device():
    if torch.cuda.is_available():
        return torch.device("cuda")
    if hasattr(torch, "xpu") and torch.xpu.is_available():
        return torch.device("xpu")
    return torch.device("cpu")


def outline(radii, phi):
    """The shape's radius at angles phi: a smooth periodic curve through the given radii
    (a Fourier series with as many terms as points), angle 0 pointing downstream."""
    r = np.asarray(radii, float)
    n = len(r)
    c = np.fft.rfft(r) / n
    out = np.full(np.shape(phi), c[0].real)
    for k in range(1, len(c)):
        scale = 1.0 if (n % 2 == 0 and k == n // 2) else 2.0
        out = out + scale * (c[k] * np.exp(1j * k * phi)).real
    return np.maximum(out, 0.02 * r.mean())


def solid_mask(radii, D, nx, ny, cx, cy):
    """Cells inside the shape, scaled to the area of a circle of diameter D; and the
    shape's extent along and across the flow, in D."""
    phi = np.linspace(0, 2 * np.pi, 4096, endpoint=False)
    rr = outline(radii, phi)
    area = 0.5 * np.mean(rr ** 2) * 2 * np.pi
    s = math.sqrt(math.pi * (D / 2) ** 2 / area)
    xs, ys = s * rr * np.cos(phi), s * rr * np.sin(phi)
    extent = (xs.min(), xs.max(), ys.min(), ys.max())
    x = np.arange(nx)[:, None] - cx
    y = np.arange(ny)[None, :] - cy
    rho = np.hypot(x, y)
    ang = np.mod(np.arctan2(y, x), 2 * np.pi)
    mask = rho <= s * outline(radii, ang)
    return mask, extent


def colormap(v):
    """Values in [0, 1] to RGB, dark blue through teal and green to yellow."""
    stops = np.array([[0.05, 0.03, 0.25], [0.15, 0.25, 0.55], [0.10, 0.55, 0.55],
                      [0.35, 0.75, 0.35], [0.98, 0.90, 0.15]])
    v = np.clip(v, 0, 1) * (len(stops) - 1)
    i = np.minimum(v.astype(int), len(stops) - 2)
    t = (v - i)[..., None]
    return (stops[i] * (1 - t) + stops[i + 1] * t)


def write_png(path, rgb):
    """rgb: (height, width, 3) floats in [0, 1]. A plain PNG, no imaging library."""
    h, w, _ = rgb.shape
    data = (np.clip(rgb, 0, 1) * 255).astype(np.uint8)
    raw = b"".join(b"\x00" + data[r].tobytes() for r in range(h))

    def chunk(kind, body):
        return (struct.pack(">I", len(body)) + kind + body
                + struct.pack(">I", zlib.crc32(kind + body) & 0xFFFFFFFF))
    with open(path, "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
                + chunk(b"IDAT", zlib.compress(raw, 6)) + chunk(b"IEND", b""))


def main(spec):
    D = int(spec["resolution"])
    Re = float(spec.get("reynolds", 100))
    nx, ny = LENGTH * D, HEIGHT * D
    cx, cy = X0 * D, HEIGHT * D // 2
    mask_np, (x0, x1, y0, y1) = solid_mask(spec["radii"], D, nx, ny, cx, cy)
    length, height = (x1 - x0) / D, (y1 - y0) / D
    if x0 < -3 * D or x1 > 10 * D or y0 < -2.4 * D or y1 > 2.4 * D:
        return {"error": f"the shape is {length:.2f} D long and {height:.2f} D tall, too big "
                         f"for the tunnel (at most 3 D ahead of its centre, 10 D behind it, "
                         f"2.4 D either side)", "length": round(length, 3),
                "height": round(height, 3)}

    dev = device()
    nu = U * D / Re
    omega = 1.0 / (3 * nu + 0.5)
    steps = STEPS_PER_D * D
    avg_from = 2 * steps // 3

    cxs = torch.tensor(CX, dtype=torch.float32, device=dev).view(9, 1, 1)
    cys = torch.tensor(CY, dtype=torch.float32, device=dev).view(9, 1, 1)
    w = torch.tensor(W, dtype=torch.float32, device=dev).view(9, 1, 1)
    solid = torch.tensor(mask_np, device=dev)
    # The solid cells next to fluid: only these ever hold populations.
    m = mask_np
    nb = np.zeros_like(m)
    for i in range(1, 9):
        nb |= np.roll(~m, (CX[i], CY[i]), axis=(0, 1))
    edge = torch.tensor((m & nb).ravel().nonzero()[0], device=dev)

    def equilibrium(rho, ux, uy):
        cu = 3 * (cxs * ux + cys * uy)
        return w * rho * (1 + cu + 0.5 * cu * cu - 1.5 * (ux * ux + uy * uy))

    ones = torch.ones((nx, ny), device=dev)
    ux0 = U * ones
    # A sideways puff just behind the shape, so a flow that would shed vortices starts
    # to at once rather than waiting for rounding error to break its symmetry.
    xx = torch.arange(nx, device=dev, dtype=torch.float32)[:, None]
    yy = torch.arange(ny, device=dev, dtype=torch.float32)[None, :]
    uy0 = 0.2 * U * torch.exp(-((xx - cx - D) ** 2 + (yy - cy - D / 4) ** 2) / (D / 2) ** 2)
    f = equilibrium(ones, ux0, uy0)
    f[:, solid] = 0
    inlet = equilibrium(torch.ones((1, ny), device=dev), U * torch.ones((1, ny), device=dev),
                        torch.zeros((1, ny), device=dev))
    cxf = torch.tensor(CX, dtype=torch.float32, device=dev).view(9, 1)
    cyf = torch.tensor(CY, dtype=torch.float32, device=dev).view(9, 1)
    opp = torch.tensor(OPP, device=dev)

    fx = torch.zeros(steps - avg_from, device=dev)
    fy = torch.zeros(steps - avg_from, device=dev)
    if dev.type == "cuda":
        torch.cuda.synchronize()
    t0 = time.time()
    for step in range(steps):
        rho = f.sum(0)
        ux = (f * cxs).sum(0) / rho
        uy = (f * cys).sum(0) / rho
        post = f - omega * (f - equilibrium(rho, ux, uy))
        post = torch.where(solid, f, post)
        for i in range(1, 9):
            f[i] = torch.roll(post[i], shifts=(CX[i], CY[i]), dims=(0, 1))
        f[0] = post[0]
        f[:, 0, :] = inlet[:, 0, :]
        f[:, -1, :] = f[:, -2, :]
        # Populations that streamed into the shape push on it, and go back the way
        # they came.
        flat = f.view(9, -1)
        hit = flat[:, edge]
        if step >= avg_from:
            k = step - avg_from
            fx[k] = 2 * (hit * cxf).sum()
            fy[k] = 2 * (hit * cyf).sum()
        flat[:, edge] = hit[opp]
    if dev.type == "cuda":
        torch.cuda.synchronize()
    wall = time.time() - t0

    q = 0.5 * U * U * D
    cd_t = (fx / q).cpu().numpy()
    cl_t = (fy / q).cpu().numpy()
    if not np.all(np.isfinite(cd_t)):
        return {"error": "the simulation became unstable (non-finite forces)"}
    # Shedding frequency from the lift's zero crossings, when it swings.
    strouhal = None
    cl = cl_t - cl_t.mean()
    if cl.std() > 0.05:
        ups = np.nonzero((cl[:-1] < 0) & (cl[1:] >= 0))[0]
        if len(ups) >= 3:
            period = np.mean(np.diff(ups))
            strouhal = round(float(D / (period * U)), 4)

    # The picture: air speed relative to the inflow, the shape black, from 1.5 D ahead
    # of the shape to 9 D behind it, 800 to 1000 pixels wide.
    rho = f.sum(0)
    speed = torch.sqrt(((f * cxs).sum(0) / rho) ** 2 + ((f * cys).sum(0) / rho) ** 2) / U
    # Cells inside the shape hold no air (zero density), so their speed is 0/0.
    sp = np.nan_to_num(speed.cpu().numpy())[cx - 3 * D // 2: cx + 9 * D, :]
    sm = mask_np[cx - 3 * D // 2: cx + 9 * D, :]
    step_px = max(1, math.ceil(sp.shape[0] / 1000))
    sp, sm = sp[::step_px, ::step_px], sm[::step_px, ::step_px]
    rgb = colormap(sp / 1.6)
    rgb[sm] = 0
    up = max(1, 800 // rgb.shape[0])        # coarse runs scaled up to be seen
    rgb = rgb.repeat(up, axis=0).repeat(up, axis=1)
    out = spec["out_dir"]
    write_png(os.path.join(out, "flow.png"), np.transpose(rgb, (1, 0, 2))[::-1])
    field = None
    if spec.get("save_field"):
        field = os.path.join(out, "field.npz")
        np.savez_compressed(field, ux=((f * cxs).sum(0) / rho).cpu().numpy().astype(np.float16),
                            uy=((f * cys).sum(0) / rho).cpu().numpy().astype(np.float16),
                            solid=mask_np, D=D, U=U)
    return {"cd": round(float(cd_t.mean()), 4), "cd_std": round(float(cd_t.std()), 4),
            "cl_rms": round(float(np.sqrt(np.mean(cl ** 2))), 4), "strouhal": strouhal,
            "length": round(length, 3), "height": round(height, 3),
            "resolution": D, "reynolds": Re, "grid": [nx, ny], "steps": steps,
            "device": torch.cuda.get_device_name() if dev.type == "cuda" else dev.type,
            "wall_seconds": round(wall, 1), "steps_per_second": round(steps / wall, 1),
            **({"field": field} if field else {})}


if __name__ == "__main__":
    with open(sys.argv[1]) as fh:
        spec = json.load(fh)
    print(json.dumps(main(spec)))
