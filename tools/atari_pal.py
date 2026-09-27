#!/usr/bin/env python3
"""atari_pal.py - authentic Atari GTIA colour palette.

Reproduces Altirra's palette generator (alt-src/Altirra/source/palettegenerator.cpp
+ gtiatables.cpp ATComputeLumaRamp) with the "Default NTSC (XL)" preset from
gtia.cpp nsATColorPresets::GetDefaultNTSCPreset(), so the mock-ups use exactly the
colours the emulator would put on screen.

  hue   = colour register high nibble (0..15)
  luma  = low nibble (bit 0 ignored by GTIA -> only 0,2,4..14 are distinct)
"""
import math
import numpy as np

# gtiatables.cpp kAltRamp (empirical 800XL resistor ladder)
LUMA_RAMP = [0.0, 0.0658340, 0.1435022, 0.2093362, 0.2750246, 0.3408586,
             0.4185267, 0.4843608, 0.5156392, 0.5814733, 0.6591414, 0.7249754,
             0.7906638, 0.8564978, 0.9341660, 1.0]

# gtia.cpp GetDefaultNTSCPreset()
NTSC = dict(hue_start=-57.0, hue_range=27.1 * 15.0,
            brightness=-0.04, contrast=1.04, saturation=0.20, srgb=True)
# gtia.cpp GetDefaultPALPreset(). Note it does NOT set mColorMatchingMode, so it
# stays None -> the generator applies NO colour matrix at all, unlike NTSC.
PAL = dict(hue_start=-12.0, hue_range=18.3 * 15.0,
           brightness=0.0, contrast=1.0, saturation=0.29, srgb=False)

# gtiatables.cpp kATPALPhaseLookup: (even phase, even invert, odd phase, odd invert)
PAL_PHASE = [(0.0, 1, 0.0, 1), (1.0, 1, 1.0, 1), (-6.0, -1, 2.0, 1),
             (-5.0, -1, -5.0, -1), (-4.0, -1, -4.0, -1), (-3.0, -1, -3.0, -1),
             (-1.0, -1, -1.0, -1), (0.0, -1, 0.0, -1), (1.0, -1, 1.0, -1),
             (-6.0, 1, 2.0, -1), (-4.0, 1, -4.0, 1), (-3.0, 1, -3.0, 1),
             (-2.0, 1, -2.0, 1), (-1.0, 1, -1.0, 1), (0.0, 1, 0.0, 1)]

CO_R = np.array([0.956, 0.621])
CO_G = np.array([-0.272, -0.647])
CO_B = np.array([-1.107, 1.704])

FROM_NTSC = np.array([[0.6068909, 0.1735011, 0.2003480],      # linear NTSC RGB -> XYZ
                      [0.2989164, 0.5865990, 0.1144845],
                      [0.0000000, 0.0660957, 1.1162243]])
TO_SRGB = np.array([[3.2404542, -1.5371385, -0.4985314],      # XYZ -> linear sRGB
                    [-0.9692660, 1.8760108, 0.0415560],
                    [0.0556434, -0.2040259, 1.0572252]])


def _srgb_encode(c):
    return np.where(c < 0.0031308, c * 12.92, 1.055 * np.power(np.maximum(c, 0), 1 / 2.4) - 0.055)


def build_palette(p=PAL):
    """-> (256,3) uint8 RGB, indexed by the Atari colour register value.
    Defaults to PAL: the hue step is 18.3 deg, not NTSC's 27.1, so the same
    colour register is a visibly different hue on a PAL machine -- picking
    4-colour palettes against the NTSC table made every blue come out green."""
    mx = TO_SRGB @ FROM_NTSC
    out = np.zeros((256, 3))
    angle = math.radians(p['hue_start'])
    step = p['hue_range'] * (2 * math.pi / (360.0 * 15.0))
    a = angle
    for hue in range(16):
        if hue == 0:
            i = q = 0.0
        elif p['srgb']:                                       # NTSC: angle advances
            i, q = p['saturation'] * math.cos(a), p['saturation'] * math.sin(a)
            a += step
        else:                                                 # PAL: phases come from
            ep, ei, op, oi = PAL_PHASE[hue - 1]               #   the lookup table, and
            a2, a3 = angle + step * ep, angle + step * op     #   even/odd lines are
            i = (math.cos(a2) * ei + math.cos(a3) * oi) * 0.5 * p['saturation']
            q = (math.sin(a2) * ei + math.sin(a3) * oi) * 0.5 * p['saturation']
        iq = np.array([i, q])
        chroma = np.array([iq @ CO_R, iq @ CO_G, iq @ CO_B])
        for luma in range(16):
            y = p['contrast'] * LUMA_RAMP[luma] + p['brightness']
            if p['srgb']:
                rgb = np.maximum(y + chroma, 0.0) ** 2.2
                rgb = _srgb_encode(mx @ rgb)
            else:
                rgb = y + chroma                              # no colour matrix
            out[hue * 16 + luma] = np.clip(rgb, 0, 1) * 255.0
    return np.round(out).astype(np.uint8)


PALETTE = build_palette()
# GTIA ignores bit 0 of a colour register -> 128 physically distinct colours
DISTINCT = np.array([h * 16 + l for h in range(16) for l in range(0, 16, 2)])
DISTINCT_RGB = PALETTE[DISTINCT]

if __name__ == "__main__":
    for name, reg in (("black $00", 0x00), ("white $0E", 0x0E), ("grey $04", 0x04),
                      ("red $34", 0x34), ("green $C6", 0xC6), ("blue $84", 0x84),
                      ("gold $1A", 0x1A)):
        print(f"{name}: {tuple(int(v) for v in PALETTE[reg])}")
    print("distinct colours:", len(DISTINCT))
