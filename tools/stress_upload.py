"""写入耐久性压力测试：连续上传同一屏内容，看能写多少次才出问题。

== 为什么要做这个测试 ==
  天气功能每 15 分钟要更新一次屏幕（一天 96 次），而已知：
    * 设备写入**失败之后**会锁死文件系统（句柄恒为 48，所有 WRITE 返回 0x55）
    * 软件无法恢复，必须给设备断电重插
    * 观察到过一次连续 6 次上传全部成功（2bpp 那轮）
  但"长期每天近百次写入会不会累积出问题"从未验证过。这个测试就是要拿到
  真实数据，而不是靠猜。

== 测试方法 ==
  用**完全相同**的 108x40 数据反复上传，每次之间只隔很短时间，
  记录每次的成功/失败以及 OPEN 返回的句柄号。
  句柄如果是递增的小值（1,2,3…）说明文件系统正常；
  一旦开始返回 48 就说明开始异常。

  为避免真正锁死设备（那样用户要拔插），脚本设了 --max 上限，
  默认 30 次，并且**一旦出现失败立即停止**。

用法：
    python stress_upload.py --max 30
    python stress_upload.py --max 60 --delay 0.5
"""
import argparse
import struct
import sys
import time

sys.path.insert(0, r"C:\Users\Darkside\Documents\nostation-hub-sync")
import render_screen as screen


def probe_handles(dev, n=3):
    """只读探测：连续 OPEN 看返回的句柄值，判断文件系统是否健康。

    不写入任何东西，所以即使设备状态不好也不会造成额外损害。
    """
    got = []
    for _ in range(n):
        r = screen.xfer(dev, struct.pack("BBBB", screen.PREFIX,
                                         screen.OPEN_FILE, 0xFF, 0)
                        + screen.AUX_FILE.encode())
        if len(r) >= 4 and r[0] == screen.PREFIX and r[1] == screen.OPEN_FILE:
            idx = r[3]
            got.append(idx)
            screen.xfer(dev, struct.pack("BBB", screen.PREFIX,
                                         screen.CLOSE_FILE, idx),
                        timeout_ms=300)
        else:
            got.append(None)
    return got


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--max", type=int, default=30)
    ap.add_argument("--delay", type=float, default=0.3)
    ap.add_argument("--text", default="19.1C 82%")
    args = ap.parse_args()

    dev = screen.open_dev()
    if not dev:
        print("设备未找到")
        return 2

    print("=== 测试前健康检查（只读）===")
    before = probe_handles(dev)
    print("  OPEN 返回句柄: {}".format(before))

    px = screen.render_text(args.text)
    payload = screen.payload_from(px)
    print("  将要重复上传: {} 字节（{}）".format(len(payload), args.text))
    print()
    print("=== 连续上传 {} 次（失败即停）===".format(args.max))

    ok_count = 0
    handles = []
    t0 = time.time()
    for i in range(1, args.max + 1):
        ok, why = screen.upload(dev, payload)
        h = probe_handles(dev, 1)
        handles.append(h[0] if h else None)
        mark = "OK " if ok else "失败"
        print("  #%-3d %s  句柄=%-4s %s" % (i, mark, handles[-1],
                                            "" if ok else why))
        if not ok:
            print()
            print("  !! 第 {} 次失败，立即停止（避免把设备彻底写死）".format(i))
            break
        ok_count += 1
        time.sleep(args.delay)

    dt = time.time() - t0
    print()
    print("=== 结果 ===")
    print("  成功次数: {} / {}".format(ok_count, args.max))
    print("  耗时: {:.1f} 秒".format(dt))
    print("  句柄序列: {}".format(handles))
    dev.close()

    if ok_count == args.max:
        print()
        print("  结论: 连续 {} 次全部成功，未出现异常。".format(ok_count))
        print("        对每 15 分钟一次（一天 96 次）的刷新来说是安全的。")
    else:
        print()
        print("  结论: 第 {} 次失败后停止。".format(ok_count + 1))
        print("        说明连续写入存在耐久上限，天气功能必须做成"
              "「只在数值变化时才写」+ 失败退避。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
