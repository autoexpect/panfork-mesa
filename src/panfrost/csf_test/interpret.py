#!/usr/bin/env python3

import os
import re
import subprocess
import sys

try:
    py_path = os.path.dirname(os.path.realpath(__file__)) + "/../bifrost/valhall"
except:
    py_path = "../bifrost/valhall"

if py_path not in sys.path:
    sys.path.insert(0, py_path)

import asm
import struct

shaders = {
    "atomic": """
IADD_IMM.i32.reconverge r0, 0x0, #0x0
NOP.wait0
ICMP.u32.ge.m1 r1, r0, u2, 0x0
BRANCHZ.eq.reconverge ^r1.h0, offset:1
BRANCHZ.eq 0x0, offset:3
ATOM1_RETURN.i32.slot0.ainc @r1, u0, offset:0x0
IADD_IMM.i32 r0, ^r0, #0x1
BRANCHZ.eq.reconverge 0x0, offset:-7
NOP.end
""",
    "rmw": """
IADD_IMM.i32.reconverge r0, 0x0, #0x0
ICMP.u32.ge.m1 r1, r0, u2, 0x0
BRANCHZ.eq.reconverge r1.h0, offset:1
BRANCHZ.eq 0x0, offset:6
NOP.wait1
LOAD.i32.unsigned.slot0.wait0 @r1, u0, offset:0
IADD_IMM.i32 r1, ^r1, #0x1
STORE.i32.slot1 @r1, u0, offset:0
IADD_IMM.i32 r0, ^r0, #0x1
BRANCHZ.eq.reconverge 0x0, offset:-9
NOP.end
""",
    "global_invocation": """
IADD_IMM.i32 r0, ^r60, #0x1
STORE.i32.slot0.end @r0, u0, offset:0
""",
    "invoc_offset": """
LSHIFT_OR.i32 r0, ^r60, 0x3020100.b22, 0x0
IADD.s32 r0, u0, ^r0
ICMP.u32.lt.i1 r1, r0, u0, 0x0
IADD.s32 r1, ^r1, u1
MOV.i32 r2, u2
STORE.i32.slot0.end @r2, ^r0, offset:0
""",
    "invoc_rmw": """
LSHIFT_OR.i32 r0, ^r60, 0x3020100.b22, 0x0
IADD.s32 r0, u0, ^r0
ICMP.u32.lt.i1 r1, r0, u0, 0x0
IADD.s32 r1, ^r1, u1
LOAD.i32.unsigned.slot0.wait0 @r2, r0, offset:0
IADD.s32 r2, ^r2, u2
STORE.i32.slot1.end @r2, ^r0, offset:0
""",

    "preframe": """
IADD_IMM.i32 r4, 0x0, #0x3f800000
IADD_IMM.i32 r5, 0x0, #0x3f000000
IADD_IMM.i32 r6, 0x0, #0x3f333333
IADD_IMM.i32 r7, 0x0, #0x3ecccccd
BLEND.slot0.v4.f32.end @r4:r5:r6:r7, blend_descriptor_0.w0, r60, target:0x0
"""
}

flg = 0xf
#flg = 0x20000f # Uncached!

memory = {
    "ev": (8192, 0x8200f),
    "x": 1024 * 1024,
    "y": 4096,
    "ls_alloc": 4096,

    "plane_0": 128 * 128 * 32, # 512 KiB
}

w = 0xffffffff

# Words are 32-bit, apart from address references
descriptors = {
    "shader": [0x118, 1 << 12, "invoc_rmw"],
    "ls": [3, 31, "ls_alloc"],
    "fau": [("ev", 0), 10, 0],
    "fau2": [("ev", 8 + (0 << 34)), 7, 0],

    "preframe_shader": [0x128, 1 << 12, "preframe"],

    "preframe_zs": [
        0x70077, # Depth/stencil type, Always for stencil tests
        0, 0, # Stencil state
        0, # unk
        # Depth source minimum, write disabled
        # [0, 1] Depth clamp
        # Depth function: Always
        (1 << 23) | (7 << 29),
        0, # Depth units
        0, # Depth factor
        0, # Depth bias clamp
    ],

    "preframe_blend": [
        # Load dest, enable
        1 | (1 << 9),
        # RGB/Alpha: Src + Zero * Src
        # All channels
        ((2 | (2 << 4) | (1 << 8)) * 0x1001) | (0xf << 28),
        # Fixed function blending, four components
        2 | (3 << 3),
        # RGBA8 TB pixel format / F32 register format
        0 | (237 << 12) | (0 << 22) | (1 << 24),
    ],

    "dcds": [
        # Clean fragment write, primitive barrier
        (1 << 9) | (1 << 10),
        # Sample mask of 0xffff, RT mask of 1
        0x1ffff,
        0, 0, # vertex array
        0, 0, # unk
        0, 0x3f800000, # min/max depth
        0, 0, # unk
        "preframe_zs", # depth/stencil
        ("preframe_blend", 1), # blend (count == 1)
        0, 0, # occlusion

        # Shader environment:
        0, # Attribute offset
        0, # FAU count
        0, 0, 0, 0, 0, 0, # unk
        0, 0, # Resources
        "preframe_shader", # Shader
        0, 0, # Thread storage
        0, 0, # FAU
    ],

    "framebuffer": [
        1, 0, # Pre/post, downscale, layer index
        0x10000, 0, # Argument
        "ls_alloc", # Sample locations
        "dcds", # DCDs
        0x007f007f, # width / height
        0, 0x007f007f, # bound min/max
        # 32x32 tile size
        # 4096 byte buffer allocation (maybe?)
        (10 << 9) | (4 << 24),
        0, # Disable S, ZS/CRC, Empty Tile, CRC
        0, # Z Clear
        0, 0, # Tiler

        # Framebuffer padding
        0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,

        # Render target
        # R8G8B8A8 internal format
        (1 << 26),
        # Write Enable
        # R8G8B8A8 colour format
        # Linear block format
        # 0123 swizzle
        # Clean pixel write enable
        1 | (19 << 3) | (2 << 8) | (0o3210 << 16) | (1 << 31),

        # AFBC overlay
        # No YTR, no split, no wide, no reverse, no front, no alpha
        # RGBA8 compression mode
        0 | (10 << 10),
        0, 0, 0, 0, 0,

        # RT Buffer
        "plane_0",
        128 * 4 // 4, # Row stride
        0x400, # Surface stride / Body offset

        # RT Clear
        0x2e234589, 0, 0, 0,
    ],
}

cmds = """
!cs 0

endpt fragment
mov x50, $ev

@ Bound min
mov w2a, 0x00000000
@ Bound max
mov w2b, 0x007f007f
mov x28, $framebuffer+1
@ Tile enable map
mov x2c, $x
mov x2e, 64

mov w40, 1
str w40, [x2c]
@str w40, [x2c, 128]

mov x52, $y
mov x58, 0x17
add x52, x52, -8
mov x5a, 4

frag:
@ Use tile enable map
fragment tem 1
@UNK 00 07, 0x51
@fragment

UNK 00 24, #0x5f0000000233
wait 1

mov x54, $plane_0
ldr x56, [x54]
wait 0

add x52, x52, 8
str x58, [x52]

@ Sometimes the fragment job doesn't seem to start at all
@ When that happens, retry a number of times
add x5a, x5a, -1
b.eq w5a, skip
b.eq w56, frag

skip:
str x56, [x52]

evstr w5f, [x50], unk 0xfd, irq

@!dump rt_buffer 0 4096
!dump y 0 4096
!dump plane_0 0 524288
@!heatmap plane_0 0 524288 gran 0x80 len 0x200 stride 0x4000
"""

docopy = """
ldr {w00-w0f}, [x52]
ldr {w10-w1f}, [x52, 64]
ldr {w20-w2f}, [x52, 128]
ldr {w30-w3f}, [x52, 192]
add x52, x52, 256

loop:
wait 0

str {w00-w0f}, [x54]
ldr {w00-w0f}, [x52]
str {w10-w1f}, [x54, 64]
ldr {w10-w1f}, [x52, 64]
str {w20-w2f}, [x54, 128]
ldr {w20-w2f}, [x52, 128]
str {w30-w3f}, [x54, 192]
ldr {w30-w3f}, [x52, 192]

add x54, x54, 256
add x52, x52, 256
add x50, x50, -256

b.ne w50, loop
b.ne w51, loop
"""

oldcmds = f"""
!cs 0

mov x50, 0x8000000

mov x52, $from
mov x54, $to
mov x56, $x
mov x58, $ev
mov x5a, $y

str cycles, [x56]
{docopy}
str cycles, [x56, 8]

UNK 00 24, #0x5f0000000233
evstr w5f, [x58], unk 0xfd, irq

!cs 1

mov x50, 0x8000000

mov x52, $from
mov x54, $to
mov x56, $x
mov x58, $ev
mov x5a, $y

add x52, x52, 0x8000000
add x54, x54, 0x8000000
add x56, x56, 32

nop
nop

str cycles, [x56]
{docopy}
str cycles, [x56, 8]

UNK 00 24, #0x5f0000000233
evstr w5f, [x58], unk 0xfd, irq

!delta x 0 4096
"""

oldcmds = """
!cs 0

@ Workgroup size 1x1x1, merging allowed
mov w21, 0x80000000

@ Workgroup count 1x1x1
mov w25, 1
mov w26, 1
mov w27, 1

@ Offset 0,0,0
mov w22, 0
mov w23, 0
mov w24, 0

@ TODO: offset x/y/z

@ Resources
mov x06, 0

@ Shader
mov x16, $shader

@ Local storage
mov x1e, $ls

@ FAU
movp x0e, $fau+0x0200000000000000

slot 2
wait 2

UNK 0400ff0000008200

mov x58, $fau
ldr x56, [x58]
wait 0

@mov w4a, 0

@slot 6
@mov x54, $x
@UNK 02 24, #0x4a0000f80211
@ldr x52, [x56]
@wait 0,1
@str x52, [x54]

mov w40, 60
1: add w40, w40, -1

@mov w4a, #0x0
@UNK 02 24, #0x4a0000f80211
@wait 1

@mov w54, #0
@UNK 00 24, #0x540000000233
@wait all

slot 2
wait 2

add w22, w22, 1
UNK 0400ff0000008200

b.ne w40, 1b

!dump x 0 4096
!dump y 0 4096
!dump ev 0 4096
"""

oldcmds = """
!cs 0

mov x48, $x

mov w21, 0x80000000
mov w25, 1
mov w26, 1
mov w27, 1

movp x0e, $fau+0x0200000000000000

@ Write FAUs
@add x0e, x48, 64
@mov x50, $ev
@str x50, [x0e]
@mov x30, 10
@str x30, [x0e, 8]
@add w0f, w0f, 0x02000000

@ Write shader descriptor
@add x16, x48, 128
@mov x30, 0x118
@str x30, [x16]
@mov x30, $compute
@str x30, [x16, 8]

wait 0

add x1e, x48, 192

mov x30, $y
@regdump x30
@mov x30, 0

endpt 1
slot 2
mov w54, #0xffffe0
UNK 00 24, #0x540000000233

wait all

mov x54, 0
mov w56, 0
mov w5d, 1

slot 2
wait 2
wait 2
regdump x30
UNK 0400ff0000008200
add x30, x30, 0x200
regdump x30
slot 2
wait 2

mov w40, 1000
1: add w40, w40, -1
str cycles, [x50, 32]
b.ne w40, 1b

wait 0
wait all

@ 6 / 10 / 14
mov w40, 1
1: add w40, w40, -1
UNK 0400ff0000000200
b.ne w40, 1b

mov w40, 1000
1: add w40, w40, -1
str cycles, [x50, 32]
b.ne w40, 1b

mov w42, 200
mov w40, 100
1: add w40, w40, -1
@wait all
@UNK 0400ff0000008001 @ compute

@UNK 0400ff0000000001
@UNK 2501504200000004 @ evadd
@UNK 3 24, #0x4a0000000211

@wait all
b.ne w40, 1b

@UNK 2601504200000004

str cycles, [x50, 40]
str cycles, [x50, 48]
UNK 02 24, #0x4a0000000211
wait 0

add x5c, x50, 64
evadd w5e, [x5c], unk 0xfd
evadd w5e, [x5c], unk 0xfd, irq, unk0

!dump x 0 4096
!dump y 0 4096
!delta ev 0 4096
"""

altcmds = """
!cs 0
!alloc x 4096
!alloc ev 4096 0x8200f
!alloc ev2 4096 0x8200f

mov x10, $x
UNK 00 30, #0x100000000000
add x12, x10, 256
str cycles, [x12]
mov x5a, $ev2
mov x48, 0
mov w4a, 0
slot 3
wait 3
UNK 00 31, 0
mov x48, $ev
mov w4a, 0x4321
add x46, x48, 64
mov w42, 0

str cycles, [x12, 8]
UNK 01 26, 0x484a00000005
str cycles, [x12, 16]
UNK 01 26, 0x484a00000005
str cycles, [x12, 24]

nop

mov w10, 10000
1:
UNK 01 26, 0x484a00000005
add w10, w10, -1
b.ne w10, 1b
str cycles, [x12, 32]

mov w10, 10000
1:
UNK 01 26, 0x484a00000005
@UNK 02 24, #0x420000000211
add w10, w10, -1
b.ne w10, 1b
str cycles, [x12, 40]

ldr x16, [x48, 0]
wait 0
str x16, [x48, 16]

UNK 00 31, 0x100000000

mov w4a, #0x0
UNK 02 24, #0x4a0000000211

mov w5e, 1
add x5c, x5a, 0x100
UNK 01 25, 0x5c5e00f80001

!delta x 0 4096
!dump ev 0 4096
!dump ev2 0 4096
"""

altcmds = """
!cs 0
!alloc x 4096
!alloc ev 4096 0x8200f

iter vertex
slot 2

mov x40, $x
mov w10, 1
mov x48, 0
mov w4a, 0
call w4a, x48
  nop
  nop
  nop
  mov x20, $.
@  movp x22, 0x0126000011223344
  movp x22, 0x1600000060000001
  str x22, [x20, 56]
  1: nop
  b 1b
  nop
  add x40, x40, #256
  regdump x40

mov x5a, #0x5ff7fd6000
mov x48, $ev
mov x40, #0x5ff7fd6000
mov w54, #0x1
UNK 00 24, #0x540000000233
wait 0
slot 6
@UNK 00 31, #0x0
UNK 00 09, #0x0
wait 6
@UNK 00 31, #0x100000000
mov x4a, x40
UNK 01 26, 0x484a00040001

!dump x 0 4096
@!dump ev 0 4096
@!delta x 0 4096
"""

cycletest = """
mov w10, 10
1:
str cycles, [x5c]
add x5c, x5c, 8
add w10, w10, -1
mov w11, 100000

inner:
add w11, w11, -1
b.ne w11, inner

b.ne w10, 1b
"""

def get_cmds(cmd):
    return cmds.replace("{cmd}", cmd)

def assemble_shader(text):
    lines = text.strip().split("\n")
    lines = [l for l in lines if len(l) > 0 and l[0] not in "#@"]
    return [asm.parse_asm(ln) for ln in lines]

class Buffer:
    id = 0

    def __init__(self):
        self.id = Buffer.id
        Buffer.id += 1

def resolve_rel(to, branch):
    return (to - branch) // 8 - 1

def to_int16(value):
    assert(value < 36768)
    assert(value >= -32768)
    return value & 0xffff

class Level(Buffer):
    def __init__(self, indent):
        super().__init__()

        self.indent = indent
        self.buffer = []
        self.call_addr_offset = None
        self.call_len_offset = None

        self.labels = {}
        self.label_refs = []
        # Numeric labels can be reused, so have to be handled specially.
        self.num_labels = {}
        self.num_refs = {}

    def offset(self):
        return len(self.buffer) * 8

    def __repr__(self):
        buf = " ".join(hex(x) for x in self.buffer)
        return f"buffer {self.id} {self.offset()} 0x200f {buf}"

    def buffer_add_value(self, offset, value):
        self.buffer[offset // 8] += value

    def process_relocs(self, refs, to=None):
        for ref, offset, type_ in refs:
            assert(type_ == "rel")

            if to is None:
                goto = self.labels[ref]
            else:
                goto = to

            value = to_int16(resolve_rel(goto, offset))
            self.buffer_add_value(offset, value)

    def finish(self):
        self.process_relocs(self.label_refs)

class Alloc(Buffer):
    def __init__(self, size, flags=0x280f):
        super().__init__()

        self.size = size
        self.flags = flags
        self.buffer = []

    def __repr__(self):
        buf = " ".join(hex(x) for x in self.buffer)
        return f"buffer {self.id} {self.size} {hex(self.flags)} {buf}"

def fmt_reloc(r, name="reloc"):
    dst, offset, src, src_offset = r
    return f"{name} {dst}+{offset} {src}+{src_offset}"

def fmt_exe(e):
    return " ".join(str(x) for x in e)

class Context:
    def __init__(self):
        self.levels = []
        self.l = None

        self.allocs = {}
        self.completed = []
        self.reloc = []
        self.reloc_split = []

        self.exe = []
        self.last_exe = None

        self.is_call = False

    def set_l(self):
        if len(self.levels):
            self.l = self.levels[-1]

    def pop_until(self, indent):
        while self.l.indent != indent:
            l = self.levels.pop()
            self.completed.append(l)

            self.set_l()
            if not len(self.levels):
                return

            buf_len = l.offset()

            r = self.l
            self.reloc.append((r.id, r.call_addr_offset * 8, l.id, 0))
            r.buffer[r.call_len_offset] = (
                (r.buffer[r.call_len_offset] & (0xffff << 48)) +
                buf_len)
            r.buffer[r.call_addr_offset] &= (0xffff << 48)

            r.call_addr_offset = None
            r.call_len_offset = None

    def flush_exe(self):
        ind = self.levels[0].indent

        self.pop_until(ind)
        if len(self.levels[0].buffer):
            l = self.levels.pop()
            l.finish()
            self.completed.append(l)

            self.levels.append(Level(ind))
            self.set_l()

        if not len(self.exe):
            return

        if self.last_exe is None:
            print("# Trying to add multiple CSs to an exe line, becoming confused")
            return

        if len(self.completed):
            p = self.completed[-1]
            assert(p.indent == ind)

            self.exe[self.last_exe] += [p.id, p.offset()]

        self.last_exe = None

    def add_shaders(self, shaders):
        for sh in shaders:
            qwords = assemble_shader(shaders[sh])
            sh = sh.lower()

            a = Alloc(len(qwords) * 8, flags=0x2017)
            a.buffer = qwords
            self.allocs[sh] = a

    def add_memory(self, memory):
        for m in memory:
            f = memory[m]
            if isinstance(f, int):
                size, flags = f, 0x280f
            else:
                size, flags = f
            self.allocs[m] = Alloc(size, flags)

    def add_descriptors(self, descriptors):
        for d in descriptors:
            words = descriptors[d]
            a = Alloc(0)

            buf = []
            for w in words:
                if isinstance(w, int):
                    buf.append(w)
                else:
                    if isinstance(w, str):
                        alloc, offset = w, 0
                    else:
                        alloc, offset = w
                    ref = self.allocs[alloc]
                    self.reloc.append((a.id, len(buf) * 4,
                                       ref.id, offset))
                    buf.append(0)
                    buf.append(0)

            it = iter(buf)
            a.buffer = [x | (y << 32) for x, y in zip(it, it)]
            a.size = len(a.buffer) * 8
            self.allocs[d] = a

    def interpret(self, text):
        text = text.split("\n")

        old_indent = None

        for orig_line in text:
            #print(orig_line, file=sys.stderr)

            line = orig_line.split("@")[0].expandtabs().rstrip().lower()
            if not line:
                continue

            indent = len(line) - len(line.lstrip())
            line = line.lstrip()

            if old_indent is None:
                self.levels.append(Level(indent))
            elif indent != old_indent:
                if indent > old_indent:
                    assert(self.is_call)

                    self.levels.append(Level(indent))
                else:
                    self.pop_until(indent)

            self.set_l()

            old_indent = indent
            self.is_call = False

            given_code = None

            # TODO: Check against this to test the disassembler?
            if re.match(r"[0-9a-f]{16} ", line):
                given_code = int(line[:16], 16)
                line = line[16:].lstrip()

            s = [x.strip(",") for x in line.split()]

            if s[0].endswith(":") or (len(s) == 1 and is_num(s[0])):
                label = s[0]
                if s[0].endswith(":"):
                    label = label[:-1]

                if is_num(label):
                    label = int(label)
                    if label in self.l.num_refs:
                        self.l.process_relocs(self.l.num_refs[label], self.l.offset())
                        del self.l.num_refs[label]
                    self.l.num_labels[label] = self.l.offset()
                else:
                    if label in self.l.labels:
                        print("Label reuse is not supported for non-numeric labels")
                    self.l.labels[label] = self.l.offset()

                s = s[1:]
                if not len(s):
                    continue

            for i in range(len(s)):
                if s[i].startswith("$"):
                    name, *offset = s[i][1:].split("+")
                    if name == ".":
                        buf = self.l
                    else:
                        buf = self.allocs[name]
                    if len(offset):
                        assert(len(offset) == 1)
                        offset = int(offset[0], 0)
                    else:
                        offset = 0

                    if s[0] == "movp":
                        rels = self.reloc_split
                    else:
                        rels = self.reloc

                    rels.append((self.l.id, self.l.offset(),
                                 buf.id, offset))
                    s[i] = "#0x0"

            def is_num(str):
                return re.fullmatch(r"[0-9]+", str)

            def hx(word):
                return int(word, 16)

            def reg(word):
                return hx(word[1:])

            def val(word):
                value = int(word.strip("#"), 0)
                assert(value < (1 << 48))
                return value

            sk = True

            if s[0] == "!cs":
                assert(len(s) == 2)
                self.flush_exe()
                self.last_exe = len(self.exe)
                self.exe.append(["exe", int(s[1])])
                continue
            elif s[0] == "!parallel":
                assert(len(s) == 2)
                self.flush_exe()
                self.last_exe = len(self.exe) - 1
                self.exe[-1] += [int(s[1])]
                continue
            elif s[0] == "!alloc":
                assert(len(s) == 3 or len(s) == 4)
                alloc_id = s[1]
                size = int(s[2])
                flags = val(s[3]) if len(s) == 4 else 0x280f
                self.allocs[alloc_id] = Alloc(size, flags)
                continue
            elif s[0] in ("!dump", "!delta"):
                assert(len(s) == 4)
                alloc_id = s[1]
                offset = val(s[2])
                size = val(s[3])
                mode = "hex" if s[0] == "!dump" else "delta"
                self.exe.append(("dump", self.allocs[alloc_id].id,
                                 offset, size, mode))
                continue
            elif s[0] == "!heatmap":
                assert(len(s) == 10)
                assert(s[4] == "gran")
                assert(s[6] == "len")
                assert(s[8] == "stride")
                alloc_id = s[1]
                offset = val(s[2])
                size = val(s[3])
                granularity = val(s[5])
                length = val(s[7])
                stride = val(s[9])
                mode = "heatmap"
                self.exe.append(("heatmap", self.allocs[alloc_id].id,
                                 offset, size, granularity, length, stride))
                continue
            elif s[0] == "movp":
                assert(len(s) == 3)
                assert(s[1][0] == "x")
                addr = reg(s[1])
                # Can't use val() as that has a max of 48 bits
                value = int(s[2].strip("#"), 0)

                self.l.buffer.append((2 << 56) | (addr << 48) | (value & 0xffffffff))
                self.l.buffer.append((2 << 56) | ((addr + 1) << 48)
                                       | ((value >> 32) & 0xffffffff))
                continue
            elif s[0] == "regdump":
                assert(len(s) == 2)
                assert(s[1][0] == "x")
                dest = reg(s[1])

                # Number of registers to write per instruction
                regs = 16

                cmd = 21
                value = (dest << 40) | (((1 << regs) - 1) << 16)

                for i in range(0, 0x60, regs):
                    code = (cmd << 56) | (i << 48) | value | (i << 2)
                    self.l.buffer.append(code)

                del cmd, value
                continue

            elif s[0] == "unk":
                if len(s) == 2:
                    h = hx(s[1])
                    cmd = h >> 56
                    addr = (h >> 48) & 0xff
                    value = h & 0xffffffffffff
                else:
                    assert(len(s) == 4)
                    cmd = hx(s[2])
                    addr = hx(s[1])
                    value = val(s[3])
            elif s[0] == "nop":
                if len(s) == 1:
                    addr = 0
                    value = 0
                    cmd = 0
                else:
                    assert(len(s) == 3)
                    addr = hx(s[1])
                    value = val(s[2])
                    cmd = 0
            elif s[0] == "mov" and s[2][0] in "xw":
                # This is actually an addition command
                assert(len(s) == 3)
                assert(s[1][0] == s[2][0])
                cmd = { "x": 17, "w": 16 }[s[1][0]]
                addr = reg(s[1])
                value = reg(s[2]) << 40
            elif s[0] == "mov":
                assert(len(s) == 3)
                cmd = { "x": 1, "w": 2 }[s[1][0]]
                addr = reg(s[1])
                value = val(s[2])
            elif s[0] == "add":
                assert(len(s) == 4)
                assert(s[1][0] == s[2][0])
                assert(s[1][0] in "wx")
                cmd = 16 if s[1][0] == "w" else 17
                addr = reg(s[1])
                value = (reg(s[2]) << 40) | (val(s[3]) & 0xffffffff)
            elif s[0] == "endpt":
                assert(len(s) == 2)
                types = {"compute": 1, "fragment": 2, "blit": 3, "vertex": 13}
                name = s[1]
                cmd = 34
                addr = 0
                value = types[name] if name in types else int(name, 0)
            elif s[0] == "fragment":
                cmd = 7
                addr = 0
                value = 0
                if len(s) != 1:
                    arg_map = {
                        "tem": {"0": 0, "1": 1},
                        "render": {
                            "z_order": 0,
                            "horizontal": 0x10,
                            "vertical": 0x20,
                            "reverse_horizontal": 0x50,
                            "reverse_vertical": 0x60,
                        },
                        "unk": {"0": 0, "1": 1 << 32},
                    }
                    for arg, val in zip(s[1::2], s[2::2]):
                        value |= arg_map[arg][val]
            elif s[0] == "wait":
                assert(len(s) == 2)
                cmd = 3
                addr = 0
                if s[1] == "all":
                    value = 255
                else:
                    value = sum(1 << int(x) for x in s[1].split(","))
                value <<= 16
            elif s[0] == "slot":
                assert(len(s) == 2)
                cmd = 23
                addr = 0
                value = int(s[1], 0)
            elif s[0] == "add":
                # TODO: unk variant
                assert(len(s) == 4)
                assert(s[1][0] == "x")
                assert(s[2][0] == "x")
                cmd = 17
                addr = reg(s[1])
                v = val(s[3])
                assert(v < (1 << 32))
                assert(v >= (-1 << 31))
                value = (reg(s[2]) << 40) | (v & 0xffffffff)
            elif s[0] == "idvs":
                assert(len(s) == 7)
                r1 = reg(s[1])
                r2 = reg(s[2])
                assert(s[3] == "mode")
                mode = int(s[4])
                assert(s[5] == "index")
                index = int(s[6])

                cmd = 6
                addr = 0
                value = (r2 << 40) | (r1 << 32) | (index << 8) | mode
            elif s[0] == "str" and s[1] in ("cycles", "timestamp"):
                assert(len(s) == 3 or len(s) == 4)
                assert(s[2][0] == "[")
                assert(s[-1][-1] == "]")
                s = [x.strip("[]") for x in s]
                assert(s[2][0] == "x")

                type_ = 1 if s[1] == "cycles" else 0
                dest = reg(s[2])
                if len(s) == 4:
                    offset = val(s[3])
                else:
                    offset = 0

                cmd = 40
                addr = 0
                value = (dest << 40) | (type_ << 32) | to_int16(offset)
            elif s[0] in ("ldr", "str"):
                reglist = s[1]
                if reglist[0] == "{":
                    end = [x[-1] for x in s].index("}")
                    reglist = s[1:end + 1]
                    s = s[:1] + s[end:]

                assert(len(s) == 3 or len(s) == 4)
                assert(s[2][0] == "[")
                assert(s[-1][-1] == "]")
                s = [x.strip("[]") for x in s]
                assert(s[2][0] == "x")

                if isinstance(reglist, str):
                    assert(reglist[0] in "xw")
                    src = reg(reglist)
                    mask = 3 if reglist[0] == "x" else 1
                else:
                    src = None
                    mask = 0

                    for r in ",".join(reglist).strip("{}").split(","):
                        r = r.split("-")
                        assert(len(r) in (1, 2))
                        regno = [reg(x) for x in r]

                        if src is None:
                            src = regno[0]

                        if len(r) == 1:
                            assert(r[0][0] in "xw")
                            new = 3 if r[0][0] == "x" else 1
                            new = (new << regno[0]) >> src
                        else:
                            assert(regno[1] > regno[0])
                            new = ((2 << regno[1]) - (1 << regno[0])) >> src

                        assert(new < (1 << 16))
                        assert(mask & new == 0)
                        mask |= new

                # Name is correct for str, but inverted for ldr
                # (The same holds for src above)
                dest = reg(s[2])
                if len(s) == 4:
                    offset = val(s[3])
                else:
                    offset = 0

                cmd = 20 if s[0] == "ldr" else 21
                addr = src
                value = (dest << 40) | (mask << 16) | to_int16(offset)
            elif s[0] == "b" or s[0].startswith("b."):
                # For unconditional jumps, use w00 as a source register if it
                # is not specified
                if s[0] == "b" and (len(s) == 2 or
                                    (len(s) == 3 and
                                     s[1] in ("back", "skip"))):
                    s = [s[0], "w00", *s[1:]]

                assert(len(s) == 3 or (len(s) == 4 and s[2] in ("back", "skip")))
                assert(s[1][0] == "w")

                ops = {
                    "b.le": 0, "b.gt": 1,
                    "b.eq": 2, "b.ne": 3,
                    "b.lt": 4, "b.ge": 5,
                    "b": 6, "b.al": 6,
                }

                src = reg(s[1])
                if len(s) == 4:
                    offset = val(s[3])
                    if s[2] == "back":
                        offset = -1 - offset
                else:
                    label = s[2]
                    if re.fullmatch(r"[0-9]+b", label):
                        label = int(label[:-1])
                        assert(label in self.l.num_labels)
                        offset = resolve_rel(self.l.num_labels[label],
                                             self.l.offset())
                    elif re.fullmatch(r"[0-9]+f", label):
                        label = int(label[:-1])
                        if label not in self.l.num_refs:
                            self.l.num_refs[label] = []
                        self.l.num_refs[label].append((label, self.l.offset(), "rel"))
                        offset = 0
                    else:
                        assert(not re.fullmatch(r"[0-9]+", label))
                        self.l.label_refs.append((label, self.l.offset(), "rel"))
                        offset = 0

                cmd = 22
                addr = 0
                value = (src << 40) | (ops[s[0]] << 28) | to_int16(offset)

            elif s[0] in ("evadd", "evstr"):
                assert(len(s) in range(5, 8))
                assert(s[1][0] in "wx")
                assert(s[2].startswith("[x"))
                assert(s[2][-1] == "]")
                assert(s[3] == "unk")
                s = [x.strip("[]()") for x in s]

                val = reg(s[1])
                dst = reg(s[2])
                mask = hx(s[4])
                irq = "irq" not in s
                unk0 = "unk0" in s

                if s[1][0] == "w":
                    cmd = 37 if s[0] == "evadd" else 38
                else:
                    cmd = 51 if s[0] == "evadd" else 52
                addr = 1
                value = ((dst << 40) | (val << 32) | (mask << 16) |
                         (irq << 2) | unk0)
            elif s[0] in ("evwait.ls", "evwait.hi"):
                assert(len(s) == 3)
                assert(s[1][0] in "wx")
                assert(s[2][0] == "[")
                assert(s[-1][-1] == "]")
                s = [x.strip("[]()") for x in s]
                src = reg(s[2])
                val = reg(s[1])
                cond = 1 if s[0] == "evwait.hi" else 0

                cmd = 53 if s[1][0] == "x" else 39
                addr = 0
                value = (src << 40) | (val << 32) | (cond << 28)
            elif s[0] == "call":
                ss = [x for x in s if x.find('(') == -1 and x.find(')') == -1]
                assert(len(ss) == 3)
                assert(ss[1][0] == "w")
                assert(ss[2][0] == "x")
                cmd = 32
                addr = 0
                num = reg(ss[1])
                target = reg(ss[2])
                value = (num << 32) | (target << 40)

                l = self.l

                cur = len(l.buffer)
                for ofs in range(cur - 2, cur):
                    if l.buffer[ofs] >> 48 == 0x100 + target:
                        l.call_addr_offset = ofs
                    if l.buffer[ofs] >> 48 == 0x200 + num:
                        l.call_len_offset = ofs
                assert(l.call_addr_offset is not None)
                assert(l.call_len_offset is not None)

                self.is_call = True
            else:
                print("Unknown command:", orig_line, file=sys.stderr)
                # TODO remove
                cmd = 0
                addr = 0
                value = 0
                sk = False
                pass

            code = (cmd << 56) | (addr << 48) | value

            if given_code and code != given_code:
                print(f"Mismatch! {hex(code)} != {hex(given_code)}, {orig_line}")

            self.l.buffer.append(code)

            del cmd, addr, value

            if False and not sk:
                print(orig_line, file=sys.stderr)
                print(indent, s, hex(code) if sk else "", file=sys.stderr)

        self.pop_until(self.levels[0].indent)
        self.flush_exe()

    def __repr__(self):
        r = []
        r += [str(self.allocs[x]) for x in self.allocs]
        r += [str(x) for x in self.completed]
        r += [fmt_reloc(x) for x in self.reloc]
        r += [fmt_reloc(x, name="relsplit") for x in self.reloc_split]
        r += [fmt_exe(x) for x in self.exe]
        return "\n".join(r)

def interpret(text):
    c = Context()
    c.add_shaders(shaders)
    c.add_memory(memory)
    c.add_descriptors(descriptors)
    c.interpret(text)
    return str(c)

def run(text):
    subprocess.run(["csf_test", "/dev/stdin"],
                   input=interpret(text), text=True)

def rebuild():
    try:
        p = subprocess.run(["rebuild-mesa"])
        if p.returncode != 0:
            return False
    except FileNotFoundError:
        pass
    return True

def go(text):
    #print(interpret(text))
    #return

    if not rebuild():
        return

    run(text)

os.environ["CSF_QUIET"] = "1"

go(get_cmds(""))

#rebuild()
#for c in range(256):
#    print(c, end=":")
#    sys.stdout.flush()
#    cmd = f"UNK 00 {hex(c)[2:]} 0x00000000"
#    run(get_cmds(cmd))

#interpret(cmds)
#go(cmds)