"""检查 exe 内嵌的图标资源：从 CArchive 里把 companion.ico 抽出来比对。

用途：确认"把 exe 挪走 / 发给别人"时窗口与任务栏图标不会丢——
图标既作为资源写进了 exe（Explorer 显示的那个），
也作为数据文件打包进了内部归档（运行时窗口图标读它）。
"""

import os
import sys

if len(sys.argv) < 2:
    print("usage: check_exe_icon.py <exe> [expected.ico]")
    sys.exit(2)

exe = sys.argv[1]
expected = sys.argv[2] if len(sys.argv) > 2 else None

try:
    from PyInstaller.archive.readers import CArchiveReader
except Exception as exc:
    print("无法导入 PyInstaller 归档读取器:", exc)
    sys.exit(3)

reader = CArchiveReader(exe)
names = list(reader.toc)
hits = [n for n in names if n.lower().endswith(".ico")]
print("exe:", exe)
print("归档条目总数:", len(names))
print("其中的 .ico 条目:", hits if hits else "（无）")

if not hits:
    print("结果: 图标 **没有** 打进 exe 内部（窗口图标在换机器后可能丢失）")
    sys.exit(1)

name = hits[0]
data = reader.extract(name)
print("内嵌图标大小: {:,} 字节".format(len(data)))

if expected and os.path.exists(expected):
    ref = open(expected, "rb").read()
    same = (data == ref)
    print("与源文件一致:", same, "（源 {} 字节）".format(len(ref)))
    if not same:
        print("结果: 内嵌图标与 companion.ico 不一致")
        sys.exit(1)

# 顺便验证抽出来的确实是合法 ICO
try:
    import io
    from PIL import Image
    with Image.open(io.BytesIO(data)) as im:
        sizes = sorted({int(s[0]) if isinstance(s, (tuple, list)) else int(s)
                        for s in (im.info.get("sizes") or [im.size])})
    print("内嵌图标尺寸:", sizes)
except Exception as exc:
    print("内嵌图标无法解析:", exc)
    sys.exit(1)

print("结果: OK —— 图标已内嵌，移动/转发都不会丢")
