#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成 PyInstaller 用的 Windows 版本资源文件（version_info.txt）。

打包后，右键 exe → 属性 → 详细信息，就能看到产品名、公司、版权等，
这也是"正式软件"和"随手写的小工具"在观感上最直接的差别。
"""

import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def read_constants():
    """按出现顺序求值 companion_app.py 里的简单字符串/整数常量。

    这样可以解析 COPYRIGHT = "...".format(AUTHOR) 这类引用其它常量的写法，
    只要被引用的常量在它前面定义过即可（实际代码里就是这样）。
    """
    src = open(os.path.join(HERE, "companion_app.py"), "r", encoding="utf-8").read()
    ns = {}
    for m in re.finditer(r"^([A-Z][A-Z0-9_]*)\s*=\s*(.+)$", src, re.M):
        name, expr = m.group(1), m.group(2).strip()
        if not (expr.startswith(("'", '"')) or expr.isdigit()):
            continue  # 跳过列表/多行字符串等复杂定义
        try:
            ns[name] = eval(expr, {"__builtins__": {}}, dict(ns))
        except Exception:
            continue
    for name in ("APP_TITLE", "APP_NAME_EN", "APP_VERSION", "APP_BUILD",
                 "AUTHOR", "COMPANY", "COPYRIGHT", "EDITION"):
        if name not in ns:
            raise SystemExit("companion_app.py 里找不到常量 {}".format(name))
    return ns


TEMPLATE = """# 由 generate_version_info.py 自动生成，请勿手工修改
VSVersionInfo(
  ffi=FixedFileInfo(
    filevers=({v0}, {v1}, {v2}, {v3}),
    prodvers=({v0}, {v1}, {v2}, {v3}),
    mask=0x3f,
    flags=0x0,
    OS=0x40004,
    fileType=0x1,
    subtype=0x0,
    date=(0, 0)
  ),
  kids=[
    StringFileInfo([
      StringTable(
        '080404B0',
        [StringStruct('CompanyName', {company!r}),
         StringStruct('FileDescription', {description!r}),
         StringStruct('FileVersion', {version!r}),
         StringStruct('InternalName', 'NostationCompanion'),
         StringStruct('LegalCopyright', {copyright!r}),
         StringStruct('LegalTrademarks', {trademarks!r}),
         StringStruct('OriginalFilename', {filename!r}),
         StringStruct('ProductName', {product!r}),
         StringStruct('ProductVersion', {version!r}),
         StringStruct('Comments', {comments!r})])
    ]),
    VarFileInfo([VarStruct('Translation', [2052, 1200])])
  ]
)
"""


def main():
    c = read_constants()

    # --print-name: 只输出带 .exe 的产品名（UTF-8 写到 stdout），供打包脚本重命名用。
    # 为什么这么做：PowerShell 5.1 读取没有 BOM 的 .ps1 会按 ANSI(GBK) 解码，
    # 脚本里的中文会当场变乱码（而且编辑一次就可能丢 BOM）。让中文名只存在于
    # Python 这一侧，打包脚本保持纯 ASCII，就不会再出这个问题。
    if "--print-name" in sys.argv:
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:
            pass
        # 文件名用的产品名（去掉标题里的空格）：Nostation自动同步伴侣.exe
        fname = c["APP_TITLE"].replace(" ", "")
        sys.stdout.write("{}.exe".format(fname))
        return

    version = c["APP_VERSION"]
    parts = [int(x) for x in version.split(".")]
    while len(parts) < 4:
        parts.append(c["APP_BUILD"] if len(parts) == 3 else 0)
    v0, v1, v2, v3 = parts[:4]

    text = TEMPLATE.format(
        v0=v0, v1=v1, v2=v2, v3=v3,
        company=c["COMPANY"],
        description="{} ({})".format(c["APP_TITLE"], c["APP_NAME_EN"]),
        version=version,
        copyright=c["COPYRIGHT"] + "  依 MIT 许可证发布，版权归 {} 所有".format(c["AUTHOR"]),
        trademarks="{} 是 {} 的标识".format(c["APP_TITLE"], c["AUTHOR"]),
        filename="{}.exe".format(c["APP_TITLE"]),
        product=c["APP_TITLE"],
        comments="By {}. {}  {}  Open Source (MIT)".format(
            c["AUTHOR"], c["COPYRIGHT"], c["EDITION"]),
    )
    out = os.path.join(HERE, "version_info.txt")
    with open(out, "w", encoding="utf-8") as fh:
        fh.write(text)
    print("written:", out)


if __name__ == "__main__":
    main()
