#!/usr/bin/env python3
"""_prof_diff_intro -- per-proc cycles/frame of two intro profile dumps (_bench_intro --dump),
each attributed through its own listing (addresses may have moved between the builds).

    python tools/_prof_diff_intro.py OLD.json OLD.lst NEW.json NEW.lst [top]
"""
import json
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _lst6502 as LS                                             # noqa: E402


def per_proc(f, lst):
    own, cur = {}, '?'
    for i in sorted((i for i in LS.read('intro', lst) if not i.data and i.mn), key=lambda i: i.addr):
        cur = next((n for n in i.labels if not n.startswith('?')), cur)
        own[i.addr] = cur
    agg = {}
    for a, c in json.load(open(f)).items():
        p = own.get(int(a), '?')
        agg[p] = agg.get(p, 0) + c
    return agg


def main():
    a = sys.argv[1:]
    old, new = per_proc(a[0], a[1]), per_proc(a[2], a[3])
    top = int(a[4]) if len(a) > 4 else 25
    keys = sorted(set(old) | set(new), key=lambda k: -abs(new.get(k, 0) - old.get(k, 0)))
    for k in keys[:top]:
        print('  %-24s %9.1f -> %9.1f  %+8.1f' % (k, old.get(k, 0), new.get(k, 0), new.get(k, 0) - old.get(k, 0)))


if __name__ == '__main__':
    main()
