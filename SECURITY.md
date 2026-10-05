# 安全说明

## 天气凭据怎么处理

本项目会用到一个可选的第三方天气服务（和风天气 QWeather）的 API Key。
这类凭据**绑定账号、带调用配额**，因此本仓库遵循一条硬规则：

> **任何凭据都不写进代码，也不进 Git 历史。**

具体做法：

| 位置 | 内容 |
| --- | --- |
| `weather.py` / `companion_app.py` | 不含任何 Key。启动时尝试 `from app_secrets import ...` |
| `app_secrets.py` | 本地私有文件，**已被 `.gitignore` 忽略**，永不入库 |
| 用户自己的机器 | 在界面「屏幕天气」分页里填自己的 Key（或直接用免费数据源） |

读不到 `app_secrets.py` 时凭据为空 —— 此时和风天气不可用，
但**免费数据源（Open-Meteo）不需要任何凭据，照常工作**。

### 本地开发时怎么放凭据

```powershell
python tools/apply_secrets.py --write --key <你的KEY> --host <你的专属HOST>
python tools/apply_secrets.py --check      # 提交前确认没有硬编码凭据
```

`tools/apply_secrets.py` 自身也**不含任何凭据** —— 需要时从命令行传入。

### 打包发布前

```powershell
python tools/build_share_exe.py
```

该脚本会临时移走 `app_secrets.py` 再打包，并在产物里**反查凭据**：
一旦搜到就判定产物不合格并删除，绝不会把凭据打进分发的 exe。

---

## 关于本仓库的历史

在 v2.1.x 开发期间，曾一度把天气服务的 Key 与专属 Host 作为默认值
**硬编码进了代码**，因此它们短暂地出现在 Git 历史中。

已做的处理：

1. 代码改为从 `app_secrets.py` 读取（该文件不入库）
2. **重写并替换了仓库历史** —— 含凭据的提交已从远端删除，不再可达
3. 打包脚本增加产物凭据反查

需要说明的是：**重写历史无法保证彻底抹除**。已经 clone 或抓取过的人
仍可能留有副本，第三方归档服务也可能存有快照。因此已建议相关凭据
**作废并重新生成** —— 对凭据泄漏来说，轮换才是真正的补救手段。

---

## 报告问题

如果你在本仓库（代码、历史、Release 附件）中发现任何凭据或敏感信息，
请开一个 Issue 说明大致位置，**不要直接把凭据原文贴出来**。

## 与厂商的关系

本项目是第三方工具，与 Matrix Lab 无隶属或背书关系。
NOSTATION 是 Matrix Lab 的产品。
