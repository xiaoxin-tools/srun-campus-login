# 绵阳城市学院 安州校区 校园网一键登录

基于 Srun SRunCGIAuthIntfSvr 认证系统的校园网一键登录工具，使用 Python + PyQt5 开发，目前适配 **绵阳城市学院** 校园网，理论上兼容所有使用同款 Srun 认证系统的学校。

![Python](https://img.shields.io/badge/Python-3.8+-blue)
![PyQt5](https://img.shields.io/badge/PyQt5-5.x-green)
![Platform](https://img.shields.io/badge/Platform-Windows-lightgrey)
![License](https://img.shields.io/badge/License-MIT-yellow)

---

## 功能特性

- 自动检测是否需要登录（通过访问外网检测 302 跳转到校园网认证 IP）
- 自动从认证服务器动态获取 base64 字母表，无需硬编码
- 支持一键登录 / 注销
- 支持记住密码
- 支持开机自启动并自动登录
- 支持设置自动登录延迟倒计时（等待开机网络初始化）
- 登录 / 注销结果通过 Windows 右下角 Toast 通知推送
- 自适应 DPI / 高分辨率屏幕（2K、4K）
- 自动从服务器拉取学校 logo 显示

## 截图

> 登录界面 / 已登录界面 / 设置界面



---

## 环境要求

- Windows 10 / 11
- Python 3.8+
- 依赖库：

```
pip install PyQt5 requests winotify
```

---

## 使用方法

### 直接运行

```bash
python campus_login.py
```

### 打包为 exe

```bash
build.bat
```

打包产物在 `dist\绵阳城市学院校园网登录.exe`，可直接分发，无需安装 Python 环境。

---

## 配置文件

配置文件存放在：

```
C:\Users\<用户名>\AppData\Roaming\MianyangCampusLogin\config.json
```

包含账号、密码（明文）、上次认证服务器地址、自动登录延迟等信息，不会污染 exe 所在目录。

---

## 适配其他学校

本工具基于通用 Srun 认证协议，如需适配其他学校，修改 `campus_login.py` 顶部的以下配置即可：

```python
AC_ID = "1"                          # 认证 AC ID，抓包获取
DOMAINS = [("互联网", "@test_sl"), ("内网", "@test_neiwang")]  # 域名选项
CHECK_URL = "https://www.msn.cn/zh-cn"  # 用于检测是否需要登录的外网地址
```

认证服务器地址（如 `http://10.8.8.117`）会在首次连接校园网时通过重定向自动检测，无需手动配置。

---

## 技术原理

1. 访问外网地址，追踪重定向链，若跳转到 IP 地址则判定为校园网拦截，记录该 IP 为认证服务器
2. 请求 `Portal.js` 动态提取 base64 自定义字母表
3. 请求 `/cgi-bin/get_challenge` 获取本次认证 token
4. 使用 HMAC-MD5 加密密码，XXTEA + 自定义 base64 加密用户信息
5. 拼接校验字符串做 SHA1 生成 chksum
6. 发起 `/cgi-bin/srun_portal?action=login` 认证请求

---

## 开源协议

[MIT License](LICENSE)

本项目仅供学习交流使用，请勿用于任何违反学校网络使用规定的行为。

---

## 作者

- 博客：[https://blog.xiaoxinbk.cn/](https://blog.xiaoxinbk.cn/)
- 如有问题欢迎提 [Issue](../../issues)
