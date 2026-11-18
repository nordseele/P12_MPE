#!/usr/bin/env python3
"""Check MPE demo files: controllers before every Note On, no overlapping
notes per channel, polyphony, and peak DIN load (bytes per second over
100 ms windows; DIN MIDI carries about 3,125). Usage: check_mpe_midi.py FILE...
"""
import sys, struct
from collections import defaultdict
def read(path):
    d=open(path,'rb').read(); assert d[:4]==b'MThd'
    ppq=struct.unpack('>H',d[12:14])[0]; i=14; assert d[i:i+4]==b'MTrk'
    n=struct.unpack('>I',d[i+4:i+8])[0]; i+=8; end=i+n; t=0; tempo=500000; ev=[]
    while i<end:
        v=0
        while True:
            b=d[i]; i+=1; v=(v<<7)|(b&0x7f)
            if b<0x80: break
        t+=v; st=d[i]
        if st==0xff:
            kind=d[i+1]; i+=2; L=0
            while True:
                b=d[i]; i+=1; L=(L<<7)|(b&0x7f)
                if b<0x80: break
            ev.append((t,'meta',d[i:i+L])); i+=L; continue
        L={0x80:3,0x90:3,0xb0:3,0xe0:3,0xd0:2,0xc0:2}[st&0xf0]
        ev.append((t,'midi',d[i:i+L])); i+=L
    return ppq,tempo,ev
for path in sys.argv[1:]:
    ppq,tempo,ev=read(path); sec=lambda t:t*tempo/1e6/ppq
    win=defaultdict(int); last_ctl=defaultdict(dict); on={}; maxpoly=0; errs=[]
    for t,k,m in ev:
        if k!='midi': continue
        win[int(sec(t)*10)]+=len(m); st=m[0]&0xf0; ch=m[0]&15
        if st==0xe0:
            last_ctl[ch]['pb']=t; v=m[1]|(m[2]<<7)
        if st==0xd0: last_ctl[ch]['z']=t
        if st==0xb0 and m[1]==74: last_ctl[ch]['y']=t
        if st==0x90 and m[2]>0:
            if ch in on: errs.append(f'overlap ch{ch+1} t={sec(t):.2f}')
            if any(last_ctl[ch].get(c)!=t for c in ('pb','z','y')): errs.append(f'no pre-controls ch{ch+1} t={sec(t):.2f}')
            on[ch]=m[1]; maxpoly=max(maxpoly,len(on))
        if st==0x80 or (st==0x90 and m[2]==0): on.pop(ch,None)
    peak=max(win.values())*10
    print(f'{path.split("/")[-1]}: {sec(ev[-1][0]):.1f}s, max poly {maxpoly}, peak {peak} B/s, errors {len(errs)} {errs[:3]}')
