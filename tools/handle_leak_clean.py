"""干净的句柄泄漏测试：上传之间**不做任何额外操作**。

== 为什么之前的结果不可信 ==
  stress_upload.py 在每次上传之后都调用 probe_handles()，
  而 probe 自己也要 OPEN + CLOSE 一次。于是每次循环实际产生了
  两次 OPEN，句柄自然是 +2。所以 8,10,12,14 这个序列
  无法区分"上传泄漏"和"探测泄漏"。

== 这次怎么测 ==
  分两组，都不在中间插入任何探测：
    组A：连续上传 N 次，全程不探测，最后才读一次句柄
    组B：只用 OPEN/CLOSE（不写入）N 次，最后才读一次句柄

  这样：
    * 若组A 的末句柄 ≈ 起始值 + N      -> 每次上传泄漏 1 个
    * 若组A 的末句柄 ≈ 起始值          -> 不泄漏，之前是探测造成的假象
    * 若组B 也增长                      -> 单纯开关文件就泄漏（固件问题）
    * 若组B 不增长而组A 增长            -> 与写入有关

  失败立即停止，避免把设备写到不可用。

用法：
    python handle_leak_clean.py --n 8
"""
import argparse
import struct
import sys
import time

sys.path.insert(0, r"C:\Users\Darkside\Documents\nostation-hub-sync")
import render_screen as screen

BAD = 48


def read_handle(dev):
    """只读地取一次当前句柄值（本身也是一次 OPEN+CLOSE，但只在组末调用）。"""
    r = screen.xfer(dev, struct.pack("BBBB", screen.PREFIX, screen.OPEN_FILE,
                                     0xFF, 0) + screen.AUX_FILE.encode())
    if len(r) >= 4 and r[0] == screen.PREFIX and r[1] == screen.OPEN_FILE:
        idx = r[3]
        screen.xfer(dev, struct.pack("BBB", screen.PREFIX, screen.CLOSE_FILE, idx),
                    timeout_ms=400)
        return idx
    return None


def group_uploads(dev, n):
    """连续上传 n 次，中间不探测。返回 (成功次数, 末句柄)。"""
    px = screen.render_text("LEAKT")
    payload = screen.payload_from(px)
    okc = 0
    for i in range(1, n + 1):
        ok, why = screen.upload(dev, payload)
        if not ok:
            print("      第 {} 次失败: {}".format(i, why))
            break
        okc += 1
        time.sleep(0.2)
    return okc, read_handle(dev)


def group_openclose(dev, n):
    """只 OPEN + CLOSE n 次，不写入。返回末句柄。"""
    last = None
    for i in range(1, n + 1):
        idx = read_handle(dev)
        last = idx
        if idx is None or idx >= BAD:
            print("      第 {} 次 OPEN 返回 {} <- 异常".format(i, idx))
            break
        time.sleep(0.15)
    return last


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=8)
    args = ap.parse_args()

    dev = screen.open_dev()
    if not dev:
        print("设备未找到")
        return 2

    print("=== 组A：连续上传 {} 次（中间不探测）===".format(args.n))
    start = read_handle(dev)
    print("      起始句柄: {}".format(start))
    if start is None or start >= BAD:
        print("      起始就异常，先给设备断电重插")
        dev.close()
        return 1
    okc, end = group_uploads(dev, args.n)
    print("      成功 {} 次，末句柄 {}".format(okc, end))
    print("      增量 = {}".format((end - start) if (end and start) else "?"))
    print()

    print("=== 组B：只 OPEN/CLOSE {} 次（不写入）===".format(args.n))
    s2 = read_handle(dev)
    print("      起始句柄: {}".format(s2))
    if s2 is not None and s2 < BAD:
        e2 = group_openclose(dev, args.n)
        print("      末句柄 {}".format(e2))
        print("      增量 = {}".format((e2 - s2) if (e2 and s2) else "?"))
    else:
        print("      设备已不可用，跳过组B")
    print()

    print("=== 判读 ===")
    if end and start:
        d1 = end - start
        print("  上传 {} 次句柄增量 = {}".format(okc, d1))
        if okc and d1 <= okc:
            print("  -> 大约每次上传占 1 个句柄")
        if okc and d1 == 0:
            print("  -> 上传不泄漏（之前的 +2 是探测造成的）")
    dev.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
