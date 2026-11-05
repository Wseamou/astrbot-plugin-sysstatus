import asyncio
import json
import os
import tempfile
import urllib.request

from astrbot.api import llm_tool, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.message_components import Image, Plain
from astrbot.api.star import Context, Star, register
from PIL import Image as PILImage, ImageDraw, ImageFont

FONT_REGULAR = "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"
FONT_BOLD = "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc"

TEXT = "#0f1f33"
MUTED = "#64748b"
BLUE = "#1e40af"
TRACK = "#e2e8f0"
GREEN = "#16a34a"
YELLOW = "#d97706"
RED = "#dc2626"


@register("astrbot_plugin_sysstatus", "moupeter", "服务器状态可视化", "1.0.0")
class SysStatusPlugin(Star):
    def __init__(self, context: Context, config=None):
        super().__init__(context)
        self.config = config or {}
        self.panel_url = str(self.config.get("panel_url") or "http://host.docker.internal:80").rstrip("/")
        self.token_file = str(self.config.get("token_file") or "/app/hoststats_token")

    def _read_token(self):
        try:
            with open(self.token_file, "r", encoding="utf-8") as f:
                return f.read().strip()
        except Exception:
            return ""

    def _fetch_status(self):
        token = self._read_token()
        req = urllib.request.Request(
            f"{self.panel_url}/api/hoststats",
            headers={"X-Hoststats-Token": token, "User-Agent": "astrbot-sysstatus"},
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode("utf-8", "ignore"))

    @staticmethod
    def _fmt_bytes(n):
        if n is None:
            return "—"
        n = int(n)
        if n >= 1024**3:
            return f"{n / 1024**3:.2f} GB"
        if n >= 1024**2:
            return f"{n / 1024**2:.2f} MB"
        if n >= 1024:
            return f"{n / 1024:.1f} KB"
        return f"{n} B"

    @staticmethod
    def _fmt_speed(bps):
        if bps is None:
            return "—"
        bps = int(bps)
        if bps >= 1024**2:
            return f"{bps / 1024**2:.2f} MB/s"
        if bps >= 1024:
            return f"{bps / 1024:.1f} KB/s"
        return f"{bps} B/s"

    def _font(self, size, bold=False):
        path = FONT_BOLD if bold else FONT_REGULAR
        try:
            return ImageFont.truetype(path, size, index=0)
        except Exception:
            return ImageFont.load_default()

    def _draw_pie(self, dr, cx, cy, r, ratio, color):
        box = [cx - r, cy - r, cx + r, cy + r]
        dr.ellipse(box, fill=TRACK)
        if ratio > 0:
            angle = 360 * max(0.0, min(1.0, ratio))
            dr.pieslice(box, -90, -90 + angle, fill=color)

    def _make_chart(self, data):
        server = data.get("server") or {}
        network = data.get("network") or {}

        mem = server.get("memory") or {}
        disk = server.get("disk") or {}
        mem_total = int(mem.get("total_mb") or 0)
        mem_used = int(mem.get("used_mb") or 0)
        disk_total = float(disk.get("total_gb") or 0)
        disk_used = float(disk.get("used_gb") or 0)
        mem_ratio = mem_used / mem_total if mem_total else 0
        disk_ratio = disk.get("use_percent") / 100 if disk.get("use_percent") else (disk_used / disk_total if disk_total else 0)

        W, H = 1080, 420
        img = PILImage.new("RGB", (W, H), "#ffffff")
        dr = ImageDraw.Draw(img)

        title_font = self._font(26, True)
        label_font = self._font(17, True)
        small_font = self._font(14)
        pie_pct_font = self._font(18, True)
        net_title_font = self._font(20, True)
        net_font = self._font(16)

        dr.text((30, 22), f"服务器状态 · {server.get('hostname', '')}", font=title_font, fill=TEXT)
        dr.text((30, 60), f"CPU {server.get('cpu', {}).get('count', '—')} 核 · {server.get('os', '')}", font=small_font, fill=MUTED)

        # Left: memory & disk pies
        mem_color = RED if mem_ratio > 0.85 else YELLOW if mem_ratio > 0.6 else BLUE
        disk_color = RED if disk_ratio > 0.85 else YELLOW if disk_ratio > 0.6 else GREEN

        self._draw_pie(dr, 150, 190, 95, mem_ratio, mem_color)
        dr.text((150, 300), "内存", font=label_font, fill=TEXT, anchor="mm")
        dr.text((150, 326), f"{mem_ratio * 100:.1f}%", font=pie_pct_font, fill=mem_color, anchor="mm")
        dr.text((150, 350), f"{mem_used} / {mem_total} MB", font=small_font, fill=MUTED, anchor="mm")

        self._draw_pie(dr, 360, 190, 95, disk_ratio, disk_color)
        dr.text((360, 300), "磁盘", font=label_font, fill=TEXT, anchor="mm")
        dr.text((360, 326), f"{disk_ratio * 100:.1f}%", font=pie_pct_font, fill=disk_color, anchor="mm")
        dr.text((360, 350), f"{disk_used} / {disk_total} GB", font=small_font, fill=MUTED, anchor="mm")

        # Right: network
        x0, y0 = 520, 80
        dr.text((x0, y0), "网络情况", font=net_title_font, fill=TEXT)
        rx_speed = self._fmt_speed(network.get("rx_speed_bps"))
        tx_speed = self._fmt_speed(network.get("tx_speed_bps"))

        rows = [
            ("下载速度", rx_speed, network.get("rx_speed_bps"), 5 * 1024**2),
            ("上传速度", tx_speed, network.get("tx_speed_bps"), 5 * 1024**2),
            ("累计接收", self._fmt_bytes(network.get("rx_bytes")), None, None),
            ("累计发送", self._fmt_bytes(network.get("tx_bytes")), None, None),
            ("TCP 连接", str(network.get("tcp_connections", "—")), None, None),
            ("Ping 223.5.5.5", f"{network.get('ping_ms')} ms" if network.get("ping_ms") is not None else "超时", None, None),
        ]

        yy = y0 + 40
        for label, value, bps, scale in rows:
            dr.text((x0, yy), label, font=net_font, fill=MUTED)
            if bps is not None and scale:
                # draw speed bar
                ratio = min(1.0, int(bps) / scale)
                bar_x = x0 + 150
                dr.rounded_rectangle([bar_x, yy + 4, bar_x + 240, yy + 16], radius=7, fill=TRACK)
                if ratio > 0:
                    dr.rounded_rectangle([bar_x, yy + 4, bar_x + 240 * ratio, yy + 16], radius=7, fill=BLUE)
                dr.text((bar_x + 250, yy), value, font=net_font, fill=TEXT)
            else:
                dr.text((x0 + 150, yy), value, font=net_font, fill=TEXT)
            yy += 46

        fd, path = tempfile.mkstemp(suffix=".png", prefix="sysstatus_")
        os.close(fd)
        img.save(path, "PNG")
        return path

    async def _render_and_send(self, event):
        data = await asyncio.to_thread(self._fetch_status)
        path = await asyncio.to_thread(self._make_chart, data)

        mem = data.get("server", {}).get("memory", {})
        disk = data.get("server", {}).get("disk", {})
        net = data.get("network", {})
        summary = (
            f"内存 {mem.get('used_mb', '—')}/{mem.get('total_mb', '—')} MB · "
            f"磁盘 {disk.get('used_gb', '—')}/{disk.get('total_gb', '—')} GB\n"
            f"↓ {self._fmt_speed(net.get('rx_speed_bps'))} ↑ {self._fmt_speed(net.get('tx_speed_bps'))} · "
            f"Ping {net.get('ping_ms')} ms"
        )
        try:
            yield event.chain_result([Plain(summary), Image.fromFileSystem(path)])
        finally:
            try:
                os.remove(path)
            except Exception:
                pass

    @filter.command("服务器状态", alias={"状态", "系统状态", "sysstatus"})
    async def cmd_status(self, event: AstrMessageEvent):
        try:
            async for r in self._render_and_send(event):
                yield r
        except Exception as exc:
            logger.error(f"[sysstatus] 生成状态图失败: {exc}")
            yield event.plain_result("服务器状态获取失败，请稍后再试。")

    @llm_tool(name="send_server_status_image")
    async def tool_status(self, event: AstrMessageEvent):
        """生成并发送服务器状态可视化图表（内存/磁盘扇形图 + 网络情况）。

        当用户询问服务器内存、磁盘占用或网络状态时调用此工具，直接在当前会话发送一张图片。
        """
        try:
            async for r in self._render_and_send(event):
                yield r
        except Exception as exc:
            logger.error(f"[sysstatus] LLM 工具生成状态图失败: {exc}")
            yield event.plain_result("服务器状态获取失败，请稍后再试。")
