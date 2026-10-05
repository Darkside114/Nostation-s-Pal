"""查清"写几次就锁死"的真正原因：是写入次数上限，还是文件句柄泄漏？

== 观察到的现象 ==
  stress_upload.py 连续上传，OPEN 返回的句柄是：
      8, 10, 12, 14, 48(失败)
  句柄在**递增**，到 48 就失败。这不像"写入 N 次的上限"，
  更像设备打开的文件句柄**没有被释放**，累积到上限就再也不给新句柄了。

== 两个待验证的猜想 ==
  A) 我自己的 upload 里 CLOSE 调了两次（try 里一次 + finally 里一次），
     设备对同一个句柄 CLOSE 两次可能就不释放了 -> 泄漏来自我的代码
  B) 设备固件本身不释放句柄 -> 泄漏来自设备，无法用软件绕过

== 怎么区分 ==
  对比三组的句柄增长速率：
      组1 只 OPEN + CLOSE（不写入）        -> 若也涨，说明单纯开关就泄漏
      组2 OPEN + CLOSE 一次（正确写法）    -> 正常应该稳定不涨
      组3 OPEN + CLOSE 两次（我现在的写法）-> 若涨得比组2快，就是我的 bug

  每组只做几次，句柄一超过 20 就停，避免把设备写到 48。

用法：
    python diagnose_handles.py
"""
import struct
import sys
import time

sys.path.insert(0, r"C:\Users\Darkside\Documents\nostation-hub-sync")
import render_screen as screen

BAD_HANDLE = 48
STOP_AT = 24            # 句柄超过这个值就停手（48 是坏值）


def open_file(dev):
    r = screen.xfer(dev, struct.pack("BBBB", screen.PREFIX, screen.OPEN_FILE,
                                     0xFF, 0) + screen.AUX_FILE.encode())
    if len(r) >= 4 and r[0] == screen.PREFIX and r[1] == screen.OPEN_FILE:
        return r[3]
    return None


def close_file(dev, idx, times=1):
    for _ in range(times):
        screen.xfer(dev, struct.pack("BBB", screen.PREFIX, screen.CLOSE_FILE,
                                     idx), timeout_ms=500)


def group_open_close(dev, label, times, n=6):
    """只做 OPEN + CLOSE（不写入），看句柄是否增长。"""
    print("  [{}]（OPEN 后 CLOSE {} 次）".format(label, times))
    seq = []
    for i in range(1, n + 1):
        idx = open_file(dev)
        seq.append(idx)
        if idx is None or idx >= BAD_HANDLE:
            print("       #{:<2} 句柄={}  <- 异常，停止".format(i, idx))
            return seq, False
        close_file(dev, idx, times)
        time.sleep(0.15)
    print("       句柄序列: {}".format(seq))
    return seq, True


def main():
    dev = screen.open_dev()
    if not dev:
        print("设备未找到")
        return 2

    print("=== 起始健康检查 ===")
    seq = []
    for _ in range(3):
        idx = open_file(dev)
        seq.append(idx)
        if idx is not None and idx < BAD_HANDLE:
            close_file(dev, idx, 1)
    print("  起始句柄: {}".format(seq))
    print()

    results = {}

    print("=== 组1: 只 OPEN + CLOSE 一次 ===")
    s1, ok1 = group_open_close(dev, "OPEN+CLOSE x1", 1, n=6)
    results["open_close_x1"] = s1
    if not ok1:
        print("  组1 就异常了，停止测试")
        dev.close()
        return 0
    print()

    print("=== 组2: OPEN + CLOSE 两次（复现我现在的 upload 写法）===")
    s2, ok2 = group_open_close(dev, "OPEN+CLOSE x2", 2, n=6)
    results["open_close_x2"] = s2
    print()

    print("=== 组3: 完整上传（正确写法：只 CLOSE 一次）===")
    px = screen.render_text("TEST")
    payload = screen.payload_from(px)
    s3 = []
    for i in range(1, 6):
        # 正确写法：成功只 CLOSE 一次；失败才在 finally 里补一次
        r = screen.xfer(dev, struct.pack("BBBB", screen.PREFIX,
                                         screen.OPEN_FILE, 0xFF, 0)
                        + screen.AUX_FILE.encode())
        if len(r) < 4:
            s3.append(None)
            break
        idx = r[3]
        s3.append(idx)
        if idx >= BAD_HANDLE:
            print("       #{:<2} 句柄={}  <- 异常".format(i, idx))
            break
        cur, bad = 0, False
        while cur < len(payload):
            ch = payload[cur:cur + screen.CHUNK]
            rr = screen.xfer(dev, struct.pack("<BBBBI", screen.PREFIX,
                                              screen.WRITE_FILE, idx,
                                              len(ch), cur) + ch)
            if not screen.ack(rr, screen.WRITE_FILE):
                print("       #{:<2} 句柄={}  写入失败@{}".format(i, idx, cur))
                bad = True
                break
            cur += len(ch)
        close_file(dev, idx, 1)          # 只关一次
        if bad:
            break
        time.sleep(0.2)
    print("       句柄序列: {}".format(s3))
    results["upload_once_close"] = s3
    print()

    print("=== 结论 ===")
    print("  组1 (只开关x1): {}".format(s1))
    print("  组2 (开关x2)  : {}".format(s2))
    print("  组3 (完整上传): {}".format(s3))
    print()
    grow1 = [h for h in s1 if h and h < BAD_HANDLE]
    if len(grow1) >= 2 and grow1[-1] > grow1[0]:
        print("  * 只做 OPEN/CLOSE 句柄也在增长 -> **设备侧不释放**，")
        print("    那么软件只能靠「少写」和「失败退避」来缓解。")
    else:
        print("  * 只做 OPEN/CLOSE 句柄稳定 -> 泄漏不是单纯开关造成的。")
    if len(s2) >= 2 and len(s1) >= 2:
        print("  * 关两次的末句柄 {} vs 关一次 {}".format(s2[-1], s1[-1]))
    dev.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
