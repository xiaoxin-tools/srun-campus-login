# coding: utf-8
"""
绵阳城市学院校园网一键登录
基于 Srun SRunCGIAuthIntfSvr 认证系统
"""
import os
import sys
import json
import hashlib
import hmac
import base64
import re
import requests
import urllib3
from urllib.parse import urlparse
from PyQt5.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QLineEdit, QPushButton, QComboBox, QCheckBox,
    QMessageBox, QFrame, QDialog
)
from PyQt5.QtCore import Qt, QThread, pyqtSignal
from PyQt5.QtGui import QFont, QPixmap

import winreg

urllib3.disable_warnings()

# ── 开机自启注册表工具 ────────────────────────────────────────────
_REG_RUN = r"Software\Microsoft\Windows\CurrentVersion\Run"
_REG_APP = "MianyangCampusLogin"


def _get_exe_path() -> str:
    """获取当前程序路径（打包后是 exe，开发时是 python + 脚本路径）"""
    if getattr(sys, 'frozen', False):
        return sys.executable
    return f'"{sys.executable}" "{os.path.abspath(__file__)}"'


def is_autostart_enabled() -> bool:
    """检查注册表中是否已设置开机自启"""
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, _REG_RUN, 0, winreg.KEY_READ)
        winreg.QueryValueEx(key, _REG_APP)
        winreg.CloseKey(key)
        return True
    except Exception:
        return False


def set_autostart(enable: bool):
    """写入或删除开机自启注册表项"""
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, _REG_RUN, 0, winreg.KEY_SET_VALUE)
        if enable:
            winreg.SetValueEx(key, _REG_APP, 0, winreg.REG_SZ, _get_exe_path())
        else:
            try:
                winreg.DeleteValue(key, _REG_APP)
            except FileNotFoundError:
                pass
        winreg.CloseKey(key)
    except Exception:
        pass

# ── 静态配置 ──────────────────────────────────────────────────────
AC_ID = "1"
# 域名选项：显示名称 -> 后缀
DOMAINS = [("互联网", "@test_sl"), ("内网", "@test_neiwang")]
# 用于检测是否需要登录的外网地址
CHECK_URL = "https://www.msn.cn/zh-cn"
# Portal.js 相对路径，用于动态提取 base64 字母表
PORTAL_JS_PATH = "/static/themes/pro/js/Portal.js"
# logo 相对路径
LOGO_PATH = "/static/themes/pro/images/logo/logo.png"
# 本地缓存文件：存放在 %APPDATA%\MianyangCampusLogin\ 下，避免污染 exe 所在目录
def _get_save_file() -> str:
    app_data = os.environ.get("APPDATA", os.path.expanduser("~"))
    save_dir = os.path.join(app_data, "MianyangCampusLogin")
    os.makedirs(save_dir, exist_ok=True)
    return os.path.join(save_dir, "config.json")

SAVE_FILE = _get_save_file()
# 标准 base64 字母表（用于构建替换映射）
_STD_ALPHA = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"
# 兜底字母表：若请求 Portal.js 失败时使用
_FALLBACK_ALPHA = "LVoJPiCN2R8G90yg+hmFHuacZ1OWMnrsSTXkYpUq/3dlbfKwv6xztjI7DeBE45QA"


# ── 动态获取 base64 字母表 ────────────────────────────────────────

def fetch_b64_alpha(auth_host: str) -> str:
    """
    请求 Portal.js，用正则提取 base64.setAlpha('...') 括号内的自定义字母表。
    提取失败时返回兜底字母表，保证流程不中断。
    """
    try:
        url = auth_host + PORTAL_JS_PATH
        r = requests.get(url, timeout=8, verify=False)
        # 匹配 base64.setAlpha('...') 或 base64.setAlpha("...")
        m = re.search(r'base64\.setAlpha\([\'"]([A-Za-z0-9+/=]{64,})[\'"]', r.text)
        if m:
            alpha = m.group(1)
            # 必须恰好 64 个字符才是合法字母表
            if len(alpha) == 64:
                return alpha
    except Exception:
        pass
    # 请求失败或未匹配到，使用兜底值
    return _FALLBACK_ALPHA


def make_b64_table(alpha: str) -> dict:
    """根据自定义字母表构建 str.maketrans 替换表"""
    return str.maketrans(_STD_ALPHA, alpha)


def fetch_logo(auth_host: str) -> bytes:
    """
    从认证服务器拉取 logo 图片，返回原始字节。
    失败或状态码非 200 时返回空字节，调用方判断为空则不显示。
    """
    try:
        r = requests.get(auth_host + LOGO_PATH, timeout=5, verify=False)
        if r.status_code == 200 and r.content:
            return r.content
    except Exception:
        pass
    return b""


def check_online_status(auth_host: str) -> tuple:
    """
    请求 srun_portal_success 页面，通过 HTML 元素判断当前登录状态。
    - 包含 id="username" 的 input -> 未登录
    - 包含 id="logout" 的 button  -> 已登录
    返回: (is_online: bool, online_username: str, online_ip: str)
    """
    try:
        r = requests.get(
            f"{auth_host}/srun_portal_success",
            params={"ac_id": "0", "theme": "pro"},
            timeout=5, verify=False
        )
        html = r.text
        # 包含注销按钮 -> 已登录
        if re.search(r'<button[^>]+id=["\']logout["\']', html):
            # 从 rad_user_info 接口获取用户名和 IP（页面 HTML 里没有用户名）
            ip = get_local_ip(auth_host)
            uname = get_online_username(auth_host)
            return True, uname, ip
        # 包含账号输入框 -> 未登录
        if re.search(r'<input[^>]+id=["\']username["\']', html):
            return False, "", ""
    except Exception:
        pass
    return False, "", ""


def do_logout(auth_host: str, username: str, ip: str) -> dict:
    """
    发起注销请求：GET /cgi-bin/srun_portal?action=logout&username=...&ip=...&ac_id=1
    """
    r = requests.get(
        f"{auth_host}/cgi-bin/srun_portal",
        params={
            "callback": "cb",
            "action": "logout",
            "username": username,
            "ip": ip,
            "ac_id": AC_ID,
            "_": "1"
        },
        timeout=10, verify=False
    )
    return _parse_jsonp(r.text)


def srun_base64(data: bytes, b64_table: dict) -> str:
    """用 srun 自定义字母表做 base64 编码"""
    return base64.b64encode(data).decode().translate(b64_table)


# ── 加密工具 ──────────────────────────────────────────────────────

def md5_with_token(password: str, token: str) -> str:
    """用 token 作为 key 对密码做 HMAC-MD5，与 JS 端 md5(password, token) 等价"""
    return hmac.new(token.encode(), password.encode(), hashlib.md5).hexdigest()


def sha1_str(s: str) -> str:
    """对字符串做 SHA1，用于生成 chksum 校验值"""
    return hashlib.sha1(s.encode()).hexdigest()


# ── XXTEA 加密（还原自 JS 端 encode 函数）────────────────────────

def _str_to_int_array(a: str, include_len: bool) -> list:
    """字符串按小端序转 int 数组，include_len=True 时末尾追加原始长度"""
    c = len(a)
    v = []
    for i in range(0, c, 4):
        v.append(
            ord(a[i]) |
            (ord(a[i + 1]) << 8 if i + 1 < c else 0) |
            (ord(a[i + 2]) << 16 if i + 2 < c else 0) |
            (ord(a[i + 3]) << 24 if i + 3 < c else 0)
        )
    if include_len:
        v.append(c)
    return v


def _int_array_to_str(a: list, include_len: bool):
    """int 数组转回字符串，include_len=True 时按末尾记录的长度截断"""
    d = len(a)
    c = (d - 1) << 2
    if include_len:
        m = a[d - 1]
        if m < c - 3 or m > c:
            return None
        c = m
    chars = []
    for i in range(d):
        chars.append(chr(a[i] & 0xff))
        chars.append(chr((a[i] >> 8) & 0xff))
        chars.append(chr((a[i] >> 16) & 0xff))
        chars.append(chr((a[i] >> 24) & 0xff))
    return ''.join(chars)[:c] if include_len else ''.join(chars)


def xxtea_encode(text: str, key: str) -> str:
    """
    XXTEA 加密，严格还原自 JS 端 encode(str, key) 函数。
    注意：m 必须分三步累加，与 JS 里三条 m += 语句等价，
    不能合并为一个表达式（Python 运算符优先级与 JS 不同会导致结果错误）。
    """
    if not text:
        return ''
    v = _str_to_int_array(text, True)
    k = _str_to_int_array(key, False)
    if len(k) < 4:
        k += [0] * (4 - len(k))
    n = len(v) - 1
    z, y = v[n], v[0]
    DELTA = 0x9E3779B9
    MASK = 0xFFFFFFFF
    q = 6 + 52 // (n + 1)
    d = 0
    for _ in range(q):
        d = (d + DELTA) & MASK
        e = (d >> 2) & 3
        for p in range(n):
            y = v[p + 1]
            # 严格按 JS 三步累加，不可合并（运算符优先级不同）
            mv  = (z >> 5) ^ (y << 2)
            mv += (y >> 3) ^ (z << 4) ^ (d ^ y)
            mv += k[(p & 3) ^ e] ^ z
            v[p] = (v[p] + mv) & MASK
            z = v[p]
        y = v[0]
        mv  = (z >> 5) ^ (y << 2)
        mv += (y >> 3) ^ (z << 4) ^ (d ^ y)
        mv += k[(n & 3) ^ e] ^ z
        v[n] = (v[n] + mv) & MASK
        z = v[n]
    return _int_array_to_str(v, False)


def encode_user_info(info: dict, token: str, b64_table: dict) -> str:
    """
    加密用户信息，对应 JS 端 _encodeUserInfo。
    流程：JSON 序列化 -> XXTEA 加密 -> srun 自定义 base64 -> 加前缀 {SRBX1}
    b64_table 由 make_b64_table(fetch_b64_alpha(...)) 动态生成。
    """
    raw = json.dumps(info, separators=(',', ':'))
    encrypted = xxtea_encode(raw, token)
    return '{SRBX1}' + srun_base64(encrypted.encode('latin-1'), b64_table)


# ── 网络检测 ──────────────────────────────────────────────────────

def _is_ip_host(url: str) -> bool:
    """判断 URL 的 host 部分是否是纯 IPv4 地址"""
    try:
        host = urlparse(url).hostname or ""
        return bool(re.fullmatch(r'\d{1,3}(\.\d{1,3}){3}', host))
    except Exception:
        return False


def check_need_login() -> tuple:
    """
    手动逐跳跟踪重定向，检测是否需要登录。
    只要任意一跳的 Location 指向 IP 地址，就认为是校园网拦截，
    并把该 IP 的 scheme+host 作为认证服务器地址返回。
    返回: (need_login: bool, auth_host: str)
    """
    session = requests.Session()
    url = CHECK_URL
    visited = set()

    for _ in range(10):  # 最多跟踪 10 跳，防止死循环
        if url in visited:
            break
        visited.add(url)
        try:
            resp = session.get(url, timeout=5, verify=False, allow_redirects=False)
        except requests.exceptions.ConnectionError:
            # 连不上外网，大概率是校园网拦截但未能拿到跳转地址
            return True, ""
        except Exception:
            return False, ""

        if resp.status_code in (301, 302, 303, 307, 308):
            loc = resp.headers.get("Location", "").strip()
            if not loc:
                break
            # 相对路径补全为绝对路径
            if loc.startswith("/"):
                p = urlparse(url)
                loc = f"{p.scheme}://{p.netloc}{loc}"
            # Location 指向 IP 地址 -> 校园网认证页
            if _is_ip_host(loc):
                p = urlparse(loc)
                auth_host = f"{p.scheme}://{p.hostname}"
                return True, auth_host
            url = loc
        else:
            # 正常响应，网络畅通
            break

    return False, ""


# ── 认证请求 ──────────────────────────────────────────────────────

def _parse_jsonp(text: str) -> dict:
    """解析 JSONP 响应，提取 JSON 对象"""
    m = re.search(r'\w+\((\{.*?\})\)', text, re.DOTALL)
    if m:
        return json.loads(m.group(1))
    return {"error": "parse_error", "raw": text}


def _save_auth_host(auth_host: str):
    """将认证服务器地址写入缓存文件，与账号密码合并保存"""
    data = {}
    try:
        with open(SAVE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        pass
    data["auth_host"] = auth_host
    with open(SAVE_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)


def get_local_ip(auth_host: str) -> str:
    """
    请求认证服务器获取本机在校园网内的 IP 地址。
    未登录时返回 client_ip，已登录时返回 online_ip，两个字段都尝试。
    """
    try:
        r = requests.get(
            f"{auth_host}/cgi-bin/rad_user_info",
            params={"callback": "cb", "_": "1"},
            timeout=5, verify=False
        )
        # 已登录时用 online_ip，未登录时用 client_ip
        for field in ("online_ip", "client_ip"):
            m = re.search(r'"' + field + r'"\s*:\s*"([^"]+)"', r.text)
            if m and m.group(1) not in ("", "0.0.0.0"):
                return m.group(1)
    except Exception:
        pass
    return ""


def get_online_username(auth_host: str) -> str:
    """
    从 rad_user_info 接口获取当前登录的用户名。
    已登录时响应里有 username 字段，未登录时没有。
    """
    try:
        r = requests.get(
            f"{auth_host}/cgi-bin/rad_user_info",
            params={"callback": "cb", "_": "1"},
            timeout=5, verify=False
        )
        m = re.search(r'"username"\s*:\s*"([^"]+)"', r.text)
        if m:
            return m.group(1)
    except Exception:
        pass
    return ""


def get_token(auth_host: str, username: str, ip: str) -> str:
    """从 get_challenge 接口获取本次认证用的 token（challenge）"""
    r = requests.get(
        f"{auth_host}/cgi-bin/get_challenge",
        params={"callback": "cb", "username": username, "ip": ip, "_": "1"},
        timeout=5, verify=False
    )
    m = re.search(r'"challenge"\s*:\s*"([^"]+)"', r.text)
    if m:
        return m.group(1)
    raise RuntimeError("获取 token 失败: " + r.text)


def do_login(auth_host: str, username: str, password: str, ip: str) -> dict:
    """
    执行完整登录流程：
    1. 从 Portal.js 动态提取 base64 字母表
    2. 获取 token
    3. HMAC-MD5 加密密码
    4. XXTEA + 动态 base64 加密用户信息
    5. 拼接 chksum 字符串并 SHA1
    6. 发起 srun_portal 认证请求
    """
    # 动态获取 base64 字母表，构建替换表
    alpha = fetch_b64_alpha(auth_host)
    b64_table = make_b64_table(alpha)

    token = get_token(auth_host, username, ip)
    hmd5 = md5_with_token(password, token)
    info = encode_user_info({
        "username": username,
        "password": password,
        "ip": ip,
        "acid": AC_ID,
        "enc_ver": "srun_bx1"
    }, token, b64_table)

    # 按 JS 端顺序拼接校验字符串
    n, type_ = "200", "1"
    chk = (token + username +
           token + hmd5 +
           token + AC_ID +
           token + ip +
           token + n +
           token + type_ +
           token + info)

    r = requests.get(
        f"{auth_host}/cgi-bin/srun_portal",
        params={
            "callback": "cb",
            "action": "login",
            "username": username,
            "password": "{MD5}" + hmd5,
            "os": "Windows 10",
            "name": "Windows",
            "double_stack": "0",
            "chksum": sha1_str(chk),
            "info": info,
            "ac_id": AC_ID,
            "ip": ip,
            "n": n,
            "type": type_,
            "_": "1"
        },
        timeout=10, verify=False
    )
    return _parse_jsonp(r.text)


# ── 后台线程 ──────────────────────────────────────────────────────

class CheckThread(QThread):
    """
    后台检测流程：
    1. 优先用缓存的 auth_host 检测在线状态（已登录则直接显示注销界面）
    2. 若缓存无效或未登录，再走外网重定向检测流程
    """
    # (need_login, auth_host, is_online, online_username, online_ip)
    result = pyqtSignal(bool, str, bool, str, str)
    logo_ready = pyqtSignal(bytes)  # logo 图片原始字节，空字节表示获取失败

    def run(self):
        # 读取缓存的认证服务器地址
        cached_host = ""
        try:
            with open(SAVE_FILE, "r", encoding="utf-8") as f:
                cached_host = json.load(f).get("auth_host", "")
        except Exception:
            pass

        # 优先用缓存地址检测在线状态
        if cached_host:
            is_online, uname, online_ip = check_online_status(cached_host)
            if is_online:
                # 已登录，直接返回，不需要再走外网检测
                self.result.emit(False, cached_host, True, uname, online_ip)
                logo_data = fetch_logo(cached_host)
                self.logo_ready.emit(logo_data)
                return

        # 缓存无效或未登录，走外网重定向检测
        need, auth_host = check_need_login()

        # 检测到新的认证服务器地址，更新缓存
        if auth_host:
            _save_auth_host(auth_host)
            logo_data = fetch_logo(auth_host)
            self.logo_ready.emit(logo_data)
        else:
            self.logo_ready.emit(b"")

        self.result.emit(need, auth_host, False, "", "")


class LogoutThread(QThread):
    """后台执行注销请求，避免阻塞 UI"""
    result = pyqtSignal(bool, str)  # (是否成功, 提示消息)

    def __init__(self, auth_host, username, ip):
        super().__init__()
        self.auth_host = auth_host
        self.username = username
        self.ip = ip

    def run(self):
        try:
            res = do_logout(self.auth_host, self.username, self.ip)
            if res.get("error") == "ok":
                self.result.emit(True, "注销成功")
            else:
                msg = res.get("error_msg") or res.get("error") or str(res)
                self.result.emit(False, "注销失败：" + msg)
        except Exception as e:
            self.result.emit(False, "错误：" + str(e))


class LoginThread(QThread):
    """后台执行登录请求，避免阻塞 UI"""
    result = pyqtSignal(bool, str)  # (是否成功, 提示消息)

    def __init__(self, auth_host, username, password):
        super().__init__()
        self.auth_host = auth_host
        self.username = username
        self.password = password

    def run(self):
        try:
            ip = get_local_ip(self.auth_host)
            if not ip:
                self.result.emit(False, "无法获取本机 IP，请确认已连接校园网")
                return
            res = do_login(self.auth_host, self.username, self.password, ip)
            if res.get("error") == "ok" or res.get("suc_msg") == "login_ok":
                self.result.emit(True, "登录成功！欢迎 " + res.get("username", self.username))
            else:
                msg = res.get("error_msg") or res.get("error") or str(res)
                self.result.emit(False, "登录失败：" + msg)
        except Exception as e:
            self.result.emit(False, "错误：" + str(e))


# ── 主窗口 UI ─────────────────────────────────────────────────────

class LoginWindow(QWidget):
    def __init__(self):
        super().__init__()
        self._auth_host = ""   # 由网络检测动态填入
        self._online_ip = ""   # 当前在线 IP，注销时使用
        self._online_user = "" # 当前在线账号
        self._auto_login = False  # 是否触发过自动登录，防止重复触发

        # 计算 DPI 缩放比例：以 96 DPI（100% 缩放）为基准
        screen = QApplication.primaryScreen()
        dpi = screen.logicalDotsPerInch()
        self._scale = dpi / 96.0

        self.setWindowTitle("绵阳城市学院 校园网登录")
        self.setFixedSize(self._s(380), self._s(420))
        self._build_ui()
        self._load_saved()
        self._auto_check()  # 启动时自动检测网络

    def _s(self, px: int) -> int:
        """将基准像素值按 DPI 缩放比例换算为实际像素"""
        return int(px * self._scale)

    def _sp(self, pt: int) -> int:
        """将基准字号（pt）按缩放比例换算，用于 QFont.setPointSize"""
        return max(1, int(pt * self._scale))

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(self._s(30), self._s(24), self._s(30), self._s(24))
        root.setSpacing(self._s(10))

        # logo 区域：检测到认证服务器后动态加载，默认隐藏
        self.logo_label = QLabel()
        self.logo_label.setAlignment(Qt.AlignCenter)
        self.logo_label.setFixedHeight(self._s(60))
        self.logo_label.hide()
        root.addWidget(self.logo_label)

        # 标题
        title = QLabel("网络准入认证")
        title.setAlignment(Qt.AlignCenter)
        f = QFont()
        f.setPointSize(self._sp(14))
        f.setBold(True)
        title.setFont(f)
        root.addWidget(title)

        # 网络状态提示
        self.status_label = QLabel("正在检测网络状态...")
        self.status_label.setAlignment(Qt.AlignCenter)
        self.status_label.setStyleSheet(f"color: gray; font-size: {self._sp(11)}pt;")
        root.addWidget(self.status_label)

        # 认证服务器地址（小字）
        self.host_label = QLabel("")
        self.host_label.setAlignment(Qt.AlignCenter)
        self.host_label.setStyleSheet(f"color: #888; font-size: {self._sp(10)}pt;")
        root.addWidget(self.host_label)

        # 分割线
        sep = QFrame()
        sep.setFrameShape(QFrame.HLine)
        sep.setFrameShadow(QFrame.Sunken)
        root.addWidget(sep)

        # ── 已登录面板（默认隐藏）────────────────────────────────
        self.online_panel = QWidget()
        op_layout = QVBoxLayout(self.online_panel)
        op_layout.setContentsMargins(0, 0, 0, 0)
        op_layout.setSpacing(self._s(8))

        self.online_label = QLabel("当前已登录")
        self.online_label.setAlignment(Qt.AlignCenter)
        self.online_label.setStyleSheet(f"color: #1976D2; font-size: {self._sp(12)}pt;")
        op_layout.addWidget(self.online_label)

        self.logout_btn = QPushButton("注销当前账号")
        self.logout_btn.setFixedHeight(self._s(36))
        self.logout_btn.setStyleSheet(
            f"QPushButton {{ background:#D32F2F; color:white; border-radius:{self._s(4)}px; font-size:{self._sp(13)}pt; }}"
            "QPushButton:hover { background:#B71C1C; }"
            "QPushButton:disabled { background:#EF9A9A; }"
        )
        self.logout_btn.clicked.connect(self._on_logout)
        op_layout.addWidget(self.logout_btn)

        self.online_panel.hide()
        root.addWidget(self.online_panel)

        # ── 登录表单（默认显示）──────────────────────────────────
        self.login_panel = QWidget()
        lp_layout = QVBoxLayout(self.login_panel)
        lp_layout.setContentsMargins(0, 0, 0, 0)
        lp_layout.setSpacing(self._s(10))

        # 账号 + 域名下拉
        row1 = QHBoxLayout()
        self.username_edit = QLineEdit()
        self.username_edit.setPlaceholderText("请输入账号")
        self.username_edit.setMinimumHeight(self._s(32))
        self.domain_combo = QComboBox()
        for label, val in DOMAINS:
            self.domain_combo.addItem(label, val)
        self.domain_combo.setFixedWidth(self._s(80))
        self.domain_combo.setMinimumHeight(self._s(32))
        row1.addWidget(self.username_edit)
        row1.addWidget(self.domain_combo)
        lp_layout.addLayout(row1)

        # 密码（回车直接触发登录）
        self.password_edit = QLineEdit()
        self.password_edit.setPlaceholderText("请输入密码")
        self.password_edit.setEchoMode(QLineEdit.Password)
        self.password_edit.setMinimumHeight(self._s(32))
        self.password_edit.returnPressed.connect(self._on_login)
        lp_layout.addWidget(self.password_edit)

        # 记住密码
        self.remember_cb = QCheckBox("记住密码")
        lp_layout.addWidget(self.remember_cb)

        # 开机自启（保留用于同步状态，但主要入口改为设置按钮）
        self.autostart_cb = QCheckBox("开机自动启动并登录")
        self.autostart_cb.setStyleSheet(f"font-size: {self._sp(10)}pt; color: #555;")
        lp_layout.addWidget(self.autostart_cb)
        self.autostart_cb.toggled.connect(self._on_autostart_toggled)

        # 登录按钮
        self.login_btn = QPushButton("登录")
        self.login_btn.setFixedHeight(self._s(36))
        self.login_btn.setStyleSheet(
            f"QPushButton {{ background:#1976D2; color:white; border-radius:{self._s(4)}px; font-size:{self._sp(13)}pt; }}"
            "QPushButton:hover { background:#1565C0; }"
            "QPushButton:disabled { background:#90CAF9; }"
        )
        self.login_btn.clicked.connect(self._on_login)
        lp_layout.addWidget(self.login_btn)

        root.addWidget(self.login_panel)

        # 底部按钮行：设置 + 重新检测
        btn_row = QHBoxLayout()

        self.settings_btn = QPushButton("设置")
        self.settings_btn.setFixedHeight(self._s(28))
        self.settings_btn.setStyleSheet(f"font-size:{self._sp(11)}pt; color:#555;")
        self.settings_btn.clicked.connect(self._open_settings)
        btn_row.addWidget(self.settings_btn)

        self.recheck_btn = QPushButton("重新检测网络")
        self.recheck_btn.setFixedHeight(self._s(28))
        self.recheck_btn.setStyleSheet(f"font-size:{self._sp(11)}pt; color:#555;")
        self.recheck_btn.clicked.connect(self._auto_check)
        btn_row.addWidget(self.recheck_btn)

        root.addLayout(btn_row)

    def _load_saved(self):
        """从本地缓存文件加载已保存的账号密码，并同步开机自启状态"""
        try:
            with open(SAVE_FILE, "r", encoding="utf-8") as f:
                d = json.load(f)
                self.username_edit.setText(d.get("username", ""))
                self.password_edit.setText(d.get("password", ""))
                idx = self.domain_combo.findData(d.get("domain", "@test_sl"))
                if idx >= 0:
                    self.domain_combo.setCurrentIndex(idx)
                if d.get("username"):
                    self.remember_cb.setChecked(True)
        except Exception:
            pass
        # 从注册表读取开机自启状态（与配置文件无关，以注册表为准）
        self.autostart_cb.setChecked(is_autostart_enabled())

    def _save_credentials(self):
        """根据记住密码选项保存或删除本地凭据（auth_host 单独由 _save_auth_host 维护）"""
        if self.remember_cb.isChecked():
            # 读取已有缓存（保留 auth_host 字段）
            data = {}
            try:
                with open(SAVE_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
            except Exception:
                pass
            data.update({
                "username": self.username_edit.text().strip(),
                "password": self.password_edit.text(),
                "domain": self.domain_combo.currentData()
            })
            with open(SAVE_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False)
        else:
            # 不记住密码时只删除账号密码字段，保留 auth_host
            data = {}
            try:
                with open(SAVE_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
            except Exception:
                pass
            for key in ("username", "password", "domain"):
                data.pop(key, None)
            if data:
                with open(SAVE_FILE, "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False)
            else:
                try:
                    os.remove(SAVE_FILE)
                except Exception:
                    pass

    def _auto_check(self):
        """启动后台线程检测网络，检测期间禁用按钮"""
        self._auth_host = ""
        self.host_label.setText("")
        self.logo_label.hide()
        self.online_panel.hide()
        self.login_panel.show()
        self.status_label.setText("正在检测网络状态...")
        self.status_label.setStyleSheet(f"color: gray; font-size: {self._sp(11)}pt;")
        self.login_btn.setEnabled(False)
        self.recheck_btn.setEnabled(False)
        self._check_thread = CheckThread()
        self._check_thread.result.connect(self._on_check_result)
        self._check_thread.logo_ready.connect(self._on_logo_ready)
        self._check_thread.start()

    def _on_logo_ready(self, logo_data: bytes):
        """logo 图片数据到达后渲染到标签，拿不到则保持隐藏"""
        if not logo_data:
            return
        pixmap = QPixmap()
        if pixmap.loadFromData(logo_data):
            max_w = self._s(320)
            max_h = self._s(60)
            pixmap = pixmap.scaled(max_w, max_h, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            self.logo_label.setPixmap(pixmap)
            self.logo_label.show()

    def _on_check_result(self, need_login, auth_host, is_online, online_username, online_ip):
        """网络检测完成回调"""
        self.recheck_btn.setEnabled(True)
        self._auth_host = auth_host

        if is_online:
            # 已登录：显示注销面板，隐藏登录表单
            self._online_user = online_username
            self._online_ip = online_ip  # 由后台线程获取，不在主线程做网络请求
            label = ("当前已登录：" + online_username) if online_username else "当前已登录"
            self.online_label.setText(label)
            self.online_panel.show()
            self.login_panel.hide()
            self.status_label.setText("网络已连通")
            self.status_label.setStyleSheet(f"color: green; font-size: {self._sp(11)}pt;")
            self.host_label.setText(("认证服务器：" + auth_host) if auth_host else "")
        elif need_login:
            self.online_panel.hide()
            self.login_panel.show()
            self.status_label.setText("未登录，请输入账号密码")
            self.status_label.setStyleSheet(f"color: #E65100; font-size: {self._sp(11)}pt;")
            if auth_host:
                self.host_label.setText("认证服务器：" + auth_host)
                self.login_btn.setEnabled(True)
                # 有保存的账号密码时自动登录
                self._try_auto_login()
            else:
                self.host_label.setText("未能获取认证服务器地址，请检查网络连接")
                self.login_btn.setEnabled(False)
        else:
            self.online_panel.hide()
            self.login_panel.show()
            self.status_label.setText("网络已连通，无需登录")
            self.status_label.setStyleSheet(f"color: green; font-size: {self._sp(11)}pt;")
            self.host_label.setText("")
            self.login_btn.setEnabled(False)

    def _on_autostart_toggled(self, checked: bool):
        """开机自启勾选框切换时写注册表"""
        set_autostart(checked)

    def _try_auto_login(self):
        """
        检测到未登录且有保存的账号密码时，自动触发一次登录。
        只在本次启动中触发一次，防止反复重试。
        """
        if self._auto_login:
            return
        username = self.username_edit.text().strip()
        password = self.password_edit.text()
        domain = self.domain_combo.currentData()
        if not username or not password or not self._auth_host:
            return
        self._auto_login = True

        # 读取延迟倒计时设置
        delay = 0
        try:
            with open(SAVE_FILE, "r", encoding="utf-8") as f:
                delay = int(json.load(f).get("auto_login_delay", 0))
        except Exception:
            pass

        if delay > 0:
            # 启动倒计时，倒计时结束后再登录
            self._countdown = delay
            self._start_countdown()
        else:
            self._do_auto_login()

    def _start_countdown(self):
        """启动倒计时定时器，每秒更新状态栏"""
        from PyQt5.QtCore import QTimer
        self._countdown_timer = QTimer(self)
        self._countdown_timer.timeout.connect(self._on_countdown_tick)
        self._countdown_timer.start(1000)
        self._update_countdown_label()

    def _on_countdown_tick(self):
        """倒计时每秒触发"""
        self._countdown -= 1
        if self._countdown <= 0:
            self._countdown_timer.stop()
            self._do_auto_login()
        else:
            self._update_countdown_label()

    def _update_countdown_label(self):
        self.status_label.setText(f"将在 {self._countdown} 秒后自动登录...")
        self.status_label.setStyleSheet(f"color: gray; font-size: {self._sp(11)}pt;")

    def _do_auto_login(self):
        """实际发起自动登录请求"""
        username = self.username_edit.text().strip()
        password = self.password_edit.text()
        domain = self.domain_combo.currentData()
        self.login_btn.setEnabled(False)
        self.login_btn.setText("自动登录中...")
        self.status_label.setText("正在自动登录...")
        self.status_label.setStyleSheet(f"color: gray; font-size: {self._sp(11)}pt;")
        self._login_thread = LoginThread(self._auth_host, username + domain, password)
        self._login_thread.result.connect(self._on_auto_login_result)
        self._login_thread.start()

    def _on_auto_login_result(self, success, msg):
        """自动登录完成回调：成功静默切换界面，失败弹通知"""
        self.login_btn.setEnabled(True)
        self.login_btn.setText("登录")
        send_notification("校园网登录成功" if success else "校园网登录失败", msg)
        if success:
            self.status_label.setText(msg)
            self.status_label.setStyleSheet(f"color: green; font-size: {self._sp(11)}pt;")
            self._auto_check()
        else:
            self.status_label.setText(msg)
            self.status_label.setStyleSheet(f"color: red; font-size: {self._sp(11)}pt;")

    def _on_login(self):
        """点击登录按钮"""
        username = self.username_edit.text().strip()
        password = self.password_edit.text()
        domain = self.domain_combo.currentData()

        if not username or not password:
            QMessageBox.warning(self, "提示", "请输入账号和密码")
            return
        if not self._auth_host:
            QMessageBox.warning(self, "提示", "未检测到认证服务器，请重新检测网络")
            return

        self.login_btn.setEnabled(False)
        self.login_btn.setText("登录中...")
        self._save_credentials()

        self._login_thread = LoginThread(self._auth_host, username + domain, password)
        self._login_thread.result.connect(self._on_login_result)
        self._login_thread.start()

    def _on_login_result(self, success, msg):
        """手动登录完成回调"""
        self.login_btn.setEnabled(True)
        self.login_btn.setText("登录")
        if success:
            self.status_label.setText(msg)
            self.status_label.setStyleSheet(f"color: green; font-size: {self._sp(11)}pt;")
            QMessageBox.information(self, "成功", msg)
            self._auto_check()
        else:
            self.status_label.setText(msg)
            self.status_label.setStyleSheet(f"color: red; font-size: {self._sp(11)}pt;")
            QMessageBox.critical(self, "登录失败", msg)

    def _on_logout(self):
        """点击注销按钮"""
        if not self._auth_host:
            QMessageBox.warning(self, "提示", "未检测到认证服务器")
            return
        if not self._online_ip:
            QMessageBox.warning(self, "提示", "无法获取当前 IP，请重新检测网络")
            return

        reply = QMessageBox.question(
            self, "确认注销",
            f"确定要注销 {self._online_user or '当前账号'} 吗？",
            QMessageBox.Yes | QMessageBox.No
        )
        if reply != QMessageBox.Yes:
            return

        self.logout_btn.setEnabled(False)
        self.logout_btn.setText("注销中...")
        self._logout_thread = LogoutThread(self._auth_host, self._online_user, self._online_ip)
        self._logout_thread.result.connect(self._on_logout_result)
        self._logout_thread.start()

    def _on_logout_result(self, success, msg):
        """注销请求完成回调"""
        self.logout_btn.setEnabled(True)
        self.logout_btn.setText("注销当前账号")
        if success:
            QMessageBox.information(self, "成功", msg)
            self._online_user = ""
            self._online_ip = ""
            self.online_panel.hide()
            self.login_panel.show()
            self.status_label.setText("未登录，请输入账号密码")
            self.status_label.setStyleSheet(f"color: #E65100; font-size: {self._sp(11)}pt;")
            self.login_btn.setEnabled(True)
        else:
            QMessageBox.critical(self, "注销失败", msg)

    def _open_settings(self):
        """打开设置对话框"""
        dlg = SettingsDialog(self)
        dlg.exec_()
        # 设置关闭后同步开机自启勾选框状态
        self.autostart_cb.blockSignals(True)
        self.autostart_cb.setChecked(is_autostart_enabled())
        self.autostart_cb.blockSignals(False)


# ── 设置对话框 ────────────────────────────────────────────────────

class SettingsDialog(QDialog):
    """独立设置窗口"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("设置")
        self.setWindowFlags(Qt.Dialog | Qt.WindowCloseButtonHint)

        # 继承主窗口缩放比例
        if parent and hasattr(parent, '_scale'):
            self._scale = parent._scale
        else:
            screen = QApplication.primaryScreen()
            self._scale = screen.logicalDotsPerInch() / 96.0

        self.setFixedSize(self._s(340), self._s(240))
        self._build_ui()
        self._load()

    def _s(self, px):
        return int(px * self._scale)

    def _sp(self, pt):
        return max(1, int(pt * self._scale))

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(self._s(24), self._s(20), self._s(24), self._s(20))
        root.setSpacing(self._s(14))

        # 标题
        title = QLabel("设置")
        f = QFont()
        f.setPointSize(self._sp(13))
        f.setBold(True)
        title.setFont(f)
        root.addWidget(title)

        sep = QFrame()
        sep.setFrameShape(QFrame.HLine)
        sep.setFrameShadow(QFrame.Sunken)
        root.addWidget(sep)

        # 开机自启
        self.autostart_cb = QCheckBox("开机自动启动并尝试登录")
        self.autostart_cb.setStyleSheet(f"font-size: {self._sp(11)}pt;")
        root.addWidget(self.autostart_cb)

        # 延迟倒计时
        delay_row = QHBoxLayout()
        delay_label = QLabel("自动登录延迟（秒）：")
        delay_label.setStyleSheet(f"font-size: {self._sp(11)}pt;")
        self.delay_spin = QComboBox()
        for s in ["0（立即）", "5", "10", "15", "20", "30", "60"]:
            self.delay_spin.addItem(s)
        self.delay_spin.setFixedWidth(self._s(110))
        self.delay_spin.setMinimumHeight(self._s(28))
        delay_row.addWidget(delay_label)
        delay_row.addWidget(self.delay_spin)
        delay_row.addStretch()
        root.addLayout(delay_row)

        hint = QLabel("延迟用于等待开机后网络初始化完成")
        hint.setStyleSheet(f"color: #888; font-size: {self._sp(9)}pt;")
        root.addWidget(hint)

        root.addStretch()

        # 保存按钮
        save_btn = QPushButton("保存")
        save_btn.setFixedHeight(self._s(34))
        save_btn.setStyleSheet(
            f"QPushButton {{ background:#1976D2; color:white; border-radius:{self._s(4)}px; font-size:{self._sp(12)}pt; }}"
            "QPushButton:hover { background:#1565C0; }"
        )
        save_btn.clicked.connect(self._save)
        root.addWidget(save_btn)

    def _load(self):
        """从配置文件和注册表加载当前设置"""
        self.autostart_cb.setChecked(is_autostart_enabled())
        delay = 0
        try:
            with open(SAVE_FILE, "r", encoding="utf-8") as f:
                delay = int(json.load(f).get("auto_login_delay", 0))
        except Exception:
            pass
        # 映射延迟值到下拉索引
        delay_map = {0: 0, 5: 1, 10: 2, 15: 3, 20: 4, 30: 5, 60: 6}
        self.delay_spin.setCurrentIndex(delay_map.get(delay, 0))

    def _save(self):
        """保存设置到注册表和配置文件"""
        # 开机自启
        set_autostart(self.autostart_cb.isChecked())

        # 延迟值
        delay_values = [0, 5, 10, 15, 20, 30, 60]
        delay = delay_values[self.delay_spin.currentIndex()]

        # 写入配置文件（保留其他字段）
        data = {}
        try:
            with open(SAVE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            pass
        data["auto_login_delay"] = delay
        with open(SAVE_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)

        self.close()


# ── Windows 通知 ──────────────────────────────────────────────────

def send_notification(title: str, msg: str):
    """
    发送 Windows 右下角 Toast 通知。
    依赖 winotify 库，导入失败时静默跳过。
    """
    try:
        from winotify import Notification
        toast = Notification(
            app_id="绵阳城市学院校园网登录",
            title=title,
            msg=msg,
            duration="short"
        )
        toast.show()
    except Exception:
        pass


# ── 入口 ──────────────────────────────────────────────────────────

if __name__ == "__main__":
    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    win = LoginWindow()
    win.show()
    sys.exit(app.exec_())
