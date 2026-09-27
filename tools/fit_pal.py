#!/usr/bin/env python3
"""fit_pal.py - the GR.7 game palette fit: for a GROUP of AW palettes that
show the same page contents (a palette switch without a redraw: fades,
flashes -- 16001's 10 -> 16 -> 13), ONE shared 16 -> 4 assignment (LUT) and
per-palette GTIA registers, by LOCAL SEARCH over the LUT with the registers
refitted per move, against the full objective summed over the members:

    colour   sum_i w_i * D2[i, R[L(i)]]           (w = pixels SHOWN under the
                                                   palette, sprites x SPR_W)
    contrast EDGE_W * sum_{i<j} adj[i,j] * max(0, C0 - Dreg[R[L(i)], R[L(j)]])
             (adjacent pixel pairs of two AW indices must stay tellable apart)
    rules    L(0) = 0 with R[0] = index 0's exact colour (the border / background
             never drifts); skin indices -> SKIN_CODE with the shared skin
             register; every code has a DISTINCT register.

Why a shared LUT: the page holds 2-bit codes computed through the LUT of the
palette in force when a shape was DRAWN; the registers of the palette in
force when the frame is SHOWN colour them. A palette that inherits a page
from another one must therefore use that one's LUT, or the codes mean the
wrong thing (that is what turned 16001's blue walls orange).

Registers are uniform over the DLI bands (seams were ruled out), so the band
table is 8 copies.
"""
import numpy as np

CODES = 4


def fit_group(members, Dreg, edge_w, c0, rng, restarts=8, iters=400, seed=None):
    """members: list of dicts
         w (16,) index weights ; adj (16,16) neighbour pairs ; D2 (16,128)
         index -> GTIA squared distance ; Caw (16,16) the original's own
         contrasts (None = c0 everywhere) ; reg0 = the pinned register
         (DISTINCT index) of code 0 ; skin_idx / skin_reg = indices forced to
         SKIN_CODE (code 2) with that register (skin_reg None = no skin here)
       Dreg (128,128) GTIA pair contrast.
       -> (lut (16,), [regs (4,) per member], energy)"""
    n = len(members)
    Cw = [np.full((16, 16), float(c0)) if m['Caw'] is None else np.minimum(c0, m['Caw'])
          for m in members]
    Wcol = [m['w'][:, None] * m['D2'] for m in members]        # (16,128) each
    used = np.zeros(16, bool)
    fixed = np.zeros(16, bool)
    fixed[0] = True
    skin_any = np.zeros(16, bool)
    for m in members:
        used |= m['w'] > 0
        if m['skin_reg'] is not None:
            fixed[m['skin_idx']] = True
            skin_any[m['skin_idx']] = True
    has_skin = bool(skin_any.any())          # code 2 reserved group-wide

    def refit(lut, regs, k):
        """member k's registers given the LUT: 2 sweeps of per-code argmin
        (colour + contrast vs the other codes' current registers), distinct."""
        m = members[k]
        adj, cw = m['adj'], Cw[k]
        for _ in range(2):
            for c in range(CODES):
                if c == 0 or (c == 2 and m['skin_reg'] is not None):
                    continue
                on = lut == c
                if not on.any():
                    continue
                cost = Wcol[k][on].sum(0).copy()
                for c2 in range(CODES):
                    if c2 == c:
                        continue
                    on2 = lut == c2
                    M = adj[on][:, on2]                   # (n1,n2) pairs
                    if M.sum() > 0:
                        want = cw[on][:, on2]             # (n1,n2)
                        d = Dreg[:, regs[c2]]             # (128,)
                        cost += edge_w * (M[:, :, None]
                                          * np.maximum(0.0, want[:, :, None] - d[None, None, :])
                                          ).sum((0, 1))
                    cost[regs[c2]] = np.inf              # distinct
                regs[c] = int(cost.argmin())
        return regs

    def refit_all(lut, regs_list):
        return [refit(lut, r.copy(), k) for k, r in enumerate(regs_list)]

    def energy(lut, regs_list):
        e = 0.0
        for k, regs in enumerate(regs_list):
            e += Wcol[k][np.arange(16), regs[lut]].sum()
            d = Dreg[regs[lut][:, None], regs[lut][None, :]]   # (16,16) shown contrast
            e += edge_w * (members[k]['adj'] * np.maximum(0.0, Cw[k] - d)).sum() / 2
        return e

    def start_regs():
        out = []
        for m in members:
            out.append(np.array([m['reg0'], 0,
                                 m['skin_reg'] if m['skin_reg'] is not None else 0, 0],
                                np.int64))
        return out

    def try_move(lut, regs_list, e):
        r2 = refit_all(lut, regs_list)
        e2 = energy(lut, r2)
        return (r2, e2, True) if e2 < e - 1e-6 else (regs_list, e, False)

    free_codes = [1, 3] if has_skin else [1, 2, 3]
    best = None
    for rs in range(restarts):
        lut = np.zeros(16, np.int64)
        # seed: the used, non-fixed indices dealt round-robin onto the free
        # codes in a random order
        cand = [i for i in range(16) if used[i] and not fixed[i]]
        rng.shuffle(cand)
        for k, i in enumerate(cand):
            lut[i] = free_codes[k % len(free_codes)]
        if rs == 0 and seed is not None:               # restart 0: the k-medoids seed
            for i in cand:
                lut[i] = int(seed[i]) if int(seed[i]) in free_codes else free_codes[0]
        lut[0] = 0
        lut[skin_any] = 2
        regs_list = refit_all(lut, start_regs())
        e = energy(lut, regs_list)
        # hill climb: single-index moves, accept any improvement
        improved = True
        it = 0
        while improved and it < iters:
            improved = False
            for i in rng.permutation(16):
                if fixed[i] or not used[i]:
                    continue
                for c in range(CODES):
                    if c == lut[i] or (c == 2 and has_skin):
                        continue
                    old = lut[i]
                    lut[i] = c
                    regs_list, e, ok = try_move(lut, regs_list, e)
                    if ok:
                        improved = True
                    else:
                        lut[i] = old
                it += 1
            # pair swaps: two indices trade codes (escapes the "either one
            # alone makes it worse" traps -- the jail's light pillar vs greys)
            for i in rng.permutation(16):
                if fixed[i] or not used[i]:
                    continue
                for j in range(i + 1, 16):
                    if fixed[j] or not used[j] or lut[i] == lut[j]:
                        continue
                    lut[i], lut[j] = lut[j], lut[i]
                    regs_list, e, ok = try_move(lut, regs_list, e)
                    if ok:
                        improved = True
                    else:
                        lut[i], lut[j] = lut[j], lut[i]
            # a whole index group moved to an EMPTY code (frees a colour)
            for c in range(1, CODES):
                if (c == 2 and has_skin) or (lut == c).any():
                    continue
                for i in rng.permutation(16):
                    if fixed[i] or not used[i]:
                        continue
                    old = lut[i]
                    lut[i] = c
                    regs_list, e, ok = try_move(lut, regs_list, e)
                    if ok:
                        improved = True
                        break
                    lut[i] = old
        if best is None or e < best[2]:
            best = (lut.copy(), [r.copy() for r in regs_list], e)
    return best


def fit_palette(w, adj, D2, Dreg, reg0, skin_idx, skin_reg, edge_w, c0,
                rng, restarts=8, iters=400, seed=None, Caw=None):
    """one palette alone (the old entry point) -> (lut, regs, energy)"""
    lut, regs, e = fit_group([dict(w=w, adj=adj, D2=D2, Caw=Caw, reg0=reg0,
                                   skin_idx=skin_idx, skin_reg=skin_reg)],
                             Dreg, edge_w, c0, rng, restarts, iters, seed)
    return lut, regs[0], e
