#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""天气屏幕控制器：抓真实温湿度并显示到 NOSTATION 辅助屏。

把三块拼在一起：
    weather.py       取数据（Open-Meteo / QWeather）
    render_screen.py 渲染 + 上传到设备
    本文件            编排：读配置 -> 取数据 -> 渲染 -> 推屏 -> 记日志 -> 定时刷新

== 用法（供 companion_app 调用）==
    WeatherScreen.refresh_once()     立刻刷新一次并推屏
    WeatherScreen.start_auto()       启动定时刷新线程
    WeatherScreen.stop_auto()        停止
"""
import datetime
import threading
import time

try:
    import weather
    import render_screen as screen
except Exception:                       # 打包/源码两种环境都能导入
    from . import weather               # type: ignore
    from . import render_screen as screen  # type: ignore

# 配置键（companion_app 的 config.json 里用这些名字）
CFG_ENABLED = "weather_enabled"
CFG_PLACE = "weather_place"
CFG_PROVIDER = "weather_provider"
CFG_QW_KEY = "weather_qweather_key"
CFG_QW_HOST = "weather_qweather_host"
CFG_INTERVAL = "weather_interval_min"
CFG_LAST_OK = "weather_last_ok"
CFG_LAST_TEXT = "weather_last_text"
CFG_LAST_ERR = "weather_last_error"
# 已推送到屏幕的内容指纹：值没变就不重复写设备
CFG_LAST_PUSH = "weather_last_pushed"
# 连续失败次数与失败时刻：写入失败后设备文件系统会锁死，
# 这时必须停止重试（重试只会继续失败，且用户必须断电重插才能恢复）
CFG_FAIL_COUNT = "weather_push_fail_count"
CFG_FAIL_AT = "weather_push_fail_at"
# 是否在推送后强制把屏幕切到"自定义位图"画面。
# 默认关闭：设备上有 SCR_MOD（切换屏幕模式）物理键，用户会自己切。
# 若程序每次都强制切模式，就会把用户的选择抢回去。
CFG_FORCE = "weather_force_custom_screen"
# 是否已经成功切过一次画面。
# 用户反馈过"软件每次刷新都会把我的 hub 屏幕切过去，不要每次都切" ——
# 所以后台定时刷新只在**还没切过**时切一次，之后即使开着上面的开关
# 也不再重复抢画面，尊重用户在设备上用物理键做的选择。
CFG_SWITCHED = "weather_screen_switched"

DEFAULT_INTERVAL_MIN = 15
MIN_INTERVAL_MIN = 5
# 推送失败后的退避：设备写入失败会锁死文件系统，只能断电重插。
# 这段时间内不再尝试推送，避免无意义地反复失败。
FAIL_BACKOFF_MIN = 30


class WeatherScreen:
    """把天气显示到屏幕。所有对外方法都不抛异常，失败写进日志。"""

    def __init__(self, log=None):
        self._log = log or (lambda msg: None)
        self._thread = None
        self._stop = threading.Event()
        self.last_result = None
        self.last_error = ""

    # -- 内部：读配置 ----------------------------------------------------
    def _cfg(self):
        try:
            import companion_app as ca
            return ca.load_config(), ca
        except Exception:
            return {}, None

    def settings(self):
        """返回当前天气设置（给界面用）。"""
        cfg, _ = self._cfg()
        return {
            "enabled": bool(cfg.get(CFG_ENABLED)),
            "place": cfg.get(CFG_PLACE, "") or "",
            "provider": cfg.get(CFG_PROVIDER, "qweather") or "qweather",
            "key": cfg.get(CFG_QW_KEY, "") or weather.DEFAULT_QW_KEY,
            "host": cfg.get(CFG_QW_HOST, "") or weather.DEFAULT_QW_HOST,
            "interval": int(cfg.get(CFG_INTERVAL) or DEFAULT_INTERVAL_MIN),
            "last_ok": cfg.get(CFG_LAST_OK, "") or "",
            "last_text": cfg.get(CFG_LAST_TEXT, "") or "",
            "last_error": cfg.get(CFG_LAST_ERR, "") or "",
            "last_pushed": cfg.get(CFG_LAST_PUSH, "") or "",
            "force_custom": bool(cfg.get(CFG_FORCE)),
            "screen_switched": bool(cfg.get(CFG_SWITCHED)),
            "fail_count": int(cfg.get(CFG_FAIL_COUNT) or 0),
            "fail_at": cfg.get(CFG_FAIL_AT, "") or "",
        }

    def save_settings(self, **kw):
        cfg, ca = self._cfg()
        if ca is None:
            return False
        mapping = {
            "enabled": CFG_ENABLED, "place": CFG_PLACE,
            "provider": CFG_PROVIDER, "key": CFG_QW_KEY, "host": CFG_QW_HOST,
            "interval": CFG_INTERVAL, "force_custom": CFG_FORCE,
            "screen_switched": CFG_SWITCHED,
        }
        for k, v in kw.items():
            if k in mapping:
                cfg[mapping[k]] = v
        ca.save_config(cfg)
        return True

    # -- 对外：刷新一次 --------------------------------------------------
    def refresh_once(self, push=True, quiet=False, force=False):
        """取一次天气并推屏。返回 (ok, 说明)。

        force=True 表示**用户主动要求**推送，此时忽略失败退避。
        退避本来只该拦住后台的自动重试（设备锁死时反复重试没意义），
        不该拦住用户在界面上的手动操作 —— 用户刚拔插过设备想重试，
        程序却因为"30 分钟内不重试"而拒绝，那是错的。
        """
        st = self.settings()
        place = st["place"].strip()
        if not place:
            msg = "没有填写地址"
            self.last_error = msg
            if not quiet:
                self._log("天气：{}".format(msg))
            return False, msg
        try:
            result = weather.fetch(place, st["provider"], st["key"], st["host"])
        except weather.WeatherError as exc:
            self.last_error = str(exc)
            self._save_err(str(exc))
            if not quiet:
                self._log("天气：取数据失败 —— {}".format(exc))
            return False, str(exc)
        except Exception as exc:
            self.last_error = "{}: {}".format(type(exc).__name__, exc)
            self._save_err(self.last_error)
            if not quiet:
                self._log("天气：异常 —— {}".format(self.last_error))
            return False, self.last_error

        self.last_result = result
        self.last_error = ""
        text = weather.describe(result)

        # ---- 只在屏幕内容真的变化时才写设备 ----------------------------
        # 为什么必须这样：设备的文件系统很脆弱，写入失败后会锁死
        # （句柄恒为 48、所有 WRITE 返回 0x55），软件无法恢复，
        # 用户必须拔插 USB。所以能少写一次就少写一次：
        # 温湿度显示值没变（比如温度仍是 19.1C、湿度仍是 82%，或地址没改）
        # 就完全跳过写入，只更新本地记录。
        fingerprint = self._screen_fingerprint(result, place)
        if push and st.get("last_pushed") == fingerprint:
            self._save_ok(text)
            if not quiet:
                self._log("天气：{}（显示值未变，跳过写屏）".format(text))
            return True, text

        if push:
            # 失败退避：设备一旦写入失败就锁死，继续重试只会白白失败。
            # force=True（用户在界面上主动点刷新）时跳过，因为用户
            # 很可能刚拔插过设备、就是要重试。
            blocked, why = self._push_blocked(st) if not force else (False, "")
            if blocked:
                self._save_ok(text)
                if not quiet:
                    self._log("天气：{}（{}）".format(text, why))
                return True, text

            ok, why = self.push(result)
            if not ok:
                self._note_push_failure(why)
                self._save_err("推屏失败：" + why)
                if not quiet:
                    self._log("天气：{}，但推屏失败 —— {}".format(text, why))
                    if "写入失败" in why or "打开文件失败" in why:
                        self._log("　　  设备存储疑似卡死，请给 NOSTATION 断电重插；"
                                  "{} 分钟内不再重试".format(FAIL_BACKOFF_MIN))
                return False, "推屏失败：" + why
            self._note_push_success(fingerprint)

        self._save_ok(text)
        if not quiet:
            self._log("天气：{}".format(text))
            if result.get("note"):
                self._log("　　  {}".format(result["note"]))
        return True, text

    # -- 写屏频率控制 ----------------------------------------------------
    @staticmethod
    def _screen_fingerprint(result, place):
        """屏幕内容指纹：值一样就不必重复写设备。"""
        return "{:.1f}|{:.0f}|{}".format(
            float(result.get("temp") or 0),
            float(result.get("humidity") or 0),
            (place or "").strip())

    def _push_blocked(self, st):
        """判断是否处在失败退避期。返回 (是否阻断, 说明)。"""
        if not st.get("fail_at"):
            return False, ""
        try:
            when = datetime.datetime.fromisoformat(st["fail_at"])
        except Exception:
            return False, ""
        elapsed = (datetime.datetime.now() - when).total_seconds() / 60.0
        if elapsed < FAIL_BACKOFF_MIN:
            left = int(FAIL_BACKOFF_MIN - elapsed)
            return True, "设备上次写屏失败，还需等待约 {} 分钟再试".format(left)
        return False, ""

    def _note_push_failure(self, why):
        cfg, ca = self._cfg()
        if ca is None:
            return
        cfg[CFG_FAIL_COUNT] = int(cfg.get(CFG_FAIL_COUNT) or 0) + 1
        cfg[CFG_FAIL_AT] = datetime.datetime.now().isoformat(timespec="seconds")
        ca.save_config(cfg)

    def _note_push_success(self, fingerprint):
        cfg, ca = self._cfg()
        if ca is None:
            return
        cfg[CFG_LAST_PUSH] = fingerprint
        cfg.pop(CFG_FAIL_COUNT, None)
        cfg.pop(CFG_FAIL_AT, None)
        ca.save_config(cfg)

    def push(self, result, want_screen=False):
        """把结果渲染并上传到辅助屏。返回 (ok, 说明)。

        == 关于抢不抢显示模式（用户明确反馈过的问题）==
          设备上有 SCR_MOD 物理键，用户会自己选画面。早期实现只要
          开了"自动切到自定义画面"，就**每次刷新都发 SET_AUX_MODE(0)**，
          等于每 15 分钟把用户选的画面顶掉 —— 用户反馈
          "软件每次刷新地区时间都会把我的 hub 屏幕切过去，不要每次都切"。

          现在改成：
            * want_screen=False（后台定时刷新）：**只在还没有成功切过一次时**
              切一次；之后即使开着开关也不再重复抢，尊重用户在设备上的选择。
            * want_screen=True（用户点了「立即刷新屏幕」）：用户明确要看天气，
              这时才每次都切过去。
        """
        try:
            # 屏幕上第一行显示解析出来的区名（如 "锦江区"）
            label = weather.short_label(result, limit=8) or ""
            px = screen.render_weather_cn(label, result["temp"],
                                          result["humidity"])
            payload = screen.payload_from(px)
            dev = screen.open_dev()
            if not dev:
                return False, "未检测到 NOSTATION"
            st = self.settings()
            mode = None
            if st.get("force_custom"):
                switched = bool(st.get("screen_switched"))
                if want_screen or not switched:
                    mode = screen.AUX_MODE_CUSTOM
            try:
                ok, why = screen.upload(dev, payload, switch_mode=mode)
            finally:
                try:
                    dev.close()
                except Exception:
                    pass
            if ok and mode is not None:
                # 记下"已经切过"，避免后台刷新反复抢画面
                self._mark_screen_switched()
            return ok, why
        except Exception as exc:
            return False, "{}: {}".format(type(exc).__name__, exc)

    def _mark_screen_switched(self):
        cfg, ca = self._cfg()
        if ca is None:
            return
        cfg[CFG_SWITCHED] = True
        ca.save_config(cfg)

    def reset_switched_flag(self):
        """清掉"已切过"标记。用户手动改过画面后再开自动切换时会用到。"""
        cfg, ca = self._cfg()
        if ca is None:
            return
        cfg.pop(CFG_SWITCHED, None)
        ca.save_config(cfg)

    def restore_builtin(self, mode=None):
        """把屏幕交还给设备自带画面。

        mode=None 时依次试设备自带的几个模式（1..5），
        停在第一个设置成功的上面 —— 因为不同固件版本自带的画面
        编号不一定一样（实测本机 mode 5 会失败，而 1~4 都正常）。
        """
        try:
            dev = screen.open_dev()
            if not dev:
                return False, "未检测到 NOSTATION"
            try:
                if mode is not None:
                    ok = screen.set_aux_mode(dev, mode)
                    return ok, ("已切到模式 %d" % mode) if ok else "设置失败"
                for m in (1, 2, 3, 4, 5, 0):
                    if screen.set_aux_mode(dev, m):
                        return True, "已切到模式 %d" % m
                return False, "所有模式都设置失败"
            finally:
                try:
                    dev.close()
                except Exception:
                    pass
        except Exception as exc:
            return False, str(exc)

    def get_mode(self):
        """读当前显示模式（只读）。"""
        try:
            dev = screen.open_dev()
            if not dev:
                return None
            try:
                return screen.get_aux_mode(dev)
            finally:
                try:
                    dev.close()
                except Exception:
                    pass
        except Exception:
            return None

    def set_mode(self, mode):
        """手动切到某个显示模式。"""
        try:
            dev = screen.open_dev()
            if not dev:
                return False, "未检测到 NOSTATION"
            try:
                ok = screen.set_aux_mode(dev, mode)
                return ok, ("已切到模式 %d" % mode) if ok else "设置失败"
            finally:
                try:
                    dev.close()
                except Exception:
                    pass
        except Exception as exc:
            return False, str(exc)

    # -- 配置记录 --------------------------------------------------------
    def _save_ok(self, text):
        cfg, ca = self._cfg()
        if ca is None:
            return
        cfg[CFG_LAST_OK] = datetime.datetime.now().isoformat(timespec="seconds")
        cfg[CFG_LAST_TEXT] = text
        cfg.pop(CFG_LAST_ERR, None)
        ca.save_config(cfg)

    def _save_err(self, err):
        cfg, ca = self._cfg()
        if ca is None:
            return
        cfg[CFG_LAST_ERR] = "{} {}".format(
            datetime.datetime.now().strftime("%H:%M:%S"), err)
        ca.save_config(cfg)

    # -- 定时刷新 --------------------------------------------------------
    def start_auto(self, on_tick=None):
        """启动定时刷新线程（已启动则忽略）。"""
        if self._thread and self._thread.is_alive():
            return False
        st = self.settings()
        if not st["enabled"] or not st["place"].strip():
            return False
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, args=(on_tick,),
                                        name="weather-auto", daemon=True)
        self._thread.start()
        self._log("天气：已启动定时刷新（每 {} 分钟）".format(st["interval"]))
        return True

    def stop_auto(self):
        self._stop.set()
        self._log("天气：已停止定时刷新")

    def is_running(self):
        return bool(self._thread and self._thread.is_alive())

    def _loop(self, on_tick):
        while not self._stop.is_set():
            try:
                self.refresh_once(push=True, quiet=False)
            except Exception as exc:
                self._log("天气：定时刷新异常 —— {}".format(exc))
            if on_tick:
                try:
                    on_tick()
                except Exception:
                    pass
            st = self.settings()
            secs = max(MIN_INTERVAL_MIN, int(st["interval"])) * 60
            if self._stop.wait(secs):
                break


# ---------------------------------------------------------------------------
# 命令行
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import sys

    def _print(msg):
        print("  " + str(msg))

    ws = WeatherScreen(log=_print)
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if "--restore" in sys.argv:
        print(ws.restore_builtin())
        sys.exit(0)
    place = " ".join(args) if args else "浦东新区"
    ws.save_settings(place=place, enabled=True)
    print("地点:", place)
    ok, why = ws.refresh_once()
    print("结果:", "成功" if ok else "失败", "-", why)
