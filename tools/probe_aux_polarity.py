"""定位"屏幕反色"：对比官方上传流程与我们的差异，并测各 mode 的极性。

== 已发现的差异（来自官方源码 amk/aux_display.py）==

    官方 on_sync_clicked（黑白屏设置/更新）：
        index = open_anim_file("BW_TEXT.ABW", False)
        while ...: write_anim_file(index, packed[cur:cur+size], cur)
        close_anim_file(index)
        # 结束 —— **不调用 apply_aux_mode**

    官方 on_dt_btn（时间同步）：
        apply_datetime(year, month, ...)
        apply_aux_mode(1)              <- 只有时间同步才切模式

  而我们的 upload() 每次都会在写完之后多发一条 SET_AUX_MODE(0)。
  用户反馈"按切换屏幕按钮有时候会给屏幕反色"，怀疑与此有关。

== 本脚本做什么 ==
  1. 读当前 mode（只读）
  2. 依次切 0..6 每个 mode 停留几秒，让你看到哪个 mode 是正常的
  3. 最后恢复 mode 5

  **不写文件**，所以不消耗设备写入寿命。

用法：
    python probe_aux_polarity.py
"""
import struct
import sys
import time

sys.path.insert(0, r"C:\Users\Darkside\Documents\nostation-hub-sync")
import render_screen as screen

GET_AUX_MODE = 0x3A


def read_mode(dev):
    r = screen.xfer(dev, struct.pack("BB", screen.PREFIX, GET_AUX_MODE))
    if len(r) >= 4 and r[0] == screen.PREFIX and r[1] == GET_AUX_MODE:
        return r[3]
    return None


def set_mode(dev, mode):
    r = screen.xfer(dev, struct.pack("BBB", screen.PREFIX,
                                     screen.SET_AUX_MODE, mode))
    return screen.ack(r, screen.SET_AUX_MODE)


def main():
    dev = screen.open_dev()
    if not dev:
        print("设备未找到")
        return 2

    print("  当前 aux mode = %s" % read_mode(dev))
    print()
    print("=== 依次切换 mode 0..6（只切模式，不写文件）===")
    print("  每个模式停留 5 秒，请留意哪个 mode 看起来「反色/不正常」")
    print()
    seen = []
    for m in range(0, 7):
        ok = set_mode(dev, m)
        time.sleep(0.4)
        back = read_mode(dev)
        seen.append((m, ok, back))
        print("  mode %d -> %s（设备读回 %s）" % (
            m, "OK" if ok else "失败", back))
        for k in range(5, 0, -1):
            print("      停留 %d 秒..." % k, end="\r")
            time.sleep(1)
        print()
    print()
    set_mode(dev, 5)
    print("  已恢复 mode 5（设备自带温湿度画面）")
    print()
    print("=== 汇总 ===")
    for m, ok, back in seen:
        print("  mode %d: 设置%s  读回%s" % (
            m, "成功" if ok else "失败", back))
    print()
    print(">>> 请告诉我：哪个 mode 号是正常的温湿度画面？")
    print(">>> 有没有哪个 mode 显示成反色（黑底变白底）？")
    dev.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
