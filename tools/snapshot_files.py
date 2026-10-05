"""快照设备文件系统，并可与上一次快照对比。

用途：让用户用官方 config.matrix-lab.com 的「黑白屏设置」上传一次内容，
然后对比文件列表变化，就能**直接看出官方工具写入的文件名与大小** ——
不用再靠猜（之前猜文件名/尺寸/格式浪费了很多轮）。

用法：
    python snapshot_files.py            # 打快照并存到 %TEMP%
    python snapshot_files.py --diff     # 与上次快照对比
"""
import json
import os
import struct
import sys
import time

import hid

MSG_LEN = 32
PREFIX = 0xFD
OK = 0xAA
GET_FS_INFO, GET_FILE_INFO = 0x23, 0x24
SNAP = os.path.join(os.environ.get("TEMP", "."), "nostation_files.json")


def open_dev():
    for d in hid.enumerate(0x4D58, 0x5748):
        if d.get("usage_page") == 0xFF60 and d.get("usage") == 0x61:
            dev = hid.device()
            dev.open_path(d["path"])
            return dev
    return None


def xfer(dev, msg, timeout_ms=900):
    body = bytes(msg) + b"\x00" * (MSG_LEN - len(msg))
    dev.write(b"\x00" + body)
    deadline = time.time() + timeout_ms / 1000.0
    while time.time() < deadline:
        r = dev.read(MSG_LEN, timeout_ms=int(timeout_ms))
        if r:
            return bytes(r)
    return b""


def main():
    dev = open_dev()
    if not dev:
        print("设备未找到")
        return 2

    r = xfer(dev, [PREFIX, GET_FS_INFO])
    fs = r.hex(" ") if r else "(超时)"
    files = {}
    if r and len(r) > 3:
        files["_fs_info"] = r.hex(" ")

    for i in range(32):
        r = xfer(dev, [PREFIX, GET_FILE_INFO, i])
        if not (len(r) > 20 and r[0] == PREFIX and r[1] == GET_FILE_INFO
                and r[2] == OK):
            break
        name = r[3:16].split(b"\x00")[0].decode("utf-8", "replace")
        size = struct.unpack("<I", r[16:20])[0]
        key = "%d:%s" % (i, name)
        files[key] = {"index": i, "name": name, "size": size,
                      "raw": r.hex(" ")[:80]}

    mode = None
    r = xfer(dev, [PREFIX, 0x3A])
    if len(r) > 3:
        mode = r[3]

    dev.close()

    print("=== 当前设备文件列表 ===")
    print("  FS info:", fs)
    print("  aux 模式:", mode)
    for k, v in files.items():
        if k == "_fs_info":
            continue
        print("  [%2d] %-20s %6d 字节" % (v["index"], v["name"], v["size"]))

    old = None
    if os.path.exists(SNAP):
        try:
            old = json.load(open(SNAP, encoding="utf-8"))
        except Exception:
            old = None
    json.dump({"files": files, "mode": mode}, open(SNAP, "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)

    if "--diff" in sys.argv:
        print()
        print("=== 与上次快照对比 ===")
        if not old:
            print("  没有上次快照，本次已记录基线")
        else:
            of = old.get("files", {})
            print("  上次 aux 模式: %s   本次: %s" % (old.get("mode"), mode))
            allk = set(of) | set(files)
            changed = False
            for k in sorted(allk):
                if k == "_fs_info":
                    continue
                a, b = of.get(k), files.get(k)
                if a is None:
                    print("  + 新增: [%s] %d 字节" % (k, b["size"]))
                    changed = True
                elif b is None:
                    print("  - 消失: [%s] (原 %d 字节)" % (k, a["size"]))
                    changed = True
                elif a["size"] != b["size"]:
                    print("  ~ 大小变化: [%s] %d -> %d 字节"
                          % (k, a["size"], b["size"]))
                    changed = True
            if not changed:
                print("  没有变化（官方工具可能还没上传，或写到了别的地方）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
