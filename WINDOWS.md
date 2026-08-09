# Windows SN2 上位机

## 要求与升级

支持 Windows 10/11、Python 3.10 或更新版本，以及 Espressif USB Serial/JTAG
CDC 设备。SN2 与旧 SN1 不兼容，必须先用新源码重新烧录 ESP32-C5。

Codex standalone CLI 用于 `account/rateLimits/read`。没有 CLI 时状态和任务摘要
仍可工作，额度只能回退到最近的 JSONL 快照。

## 安装

```powershell
Set-Location <state_notifier目录>
powershell -ExecutionPolicy Bypass -File .\host\setup_windows.ps1
powershell -ExecutionPolicy Bypass -File .\host\install_hooks.ps1 install
```

安装器创建 `host\.venv`，安装 pyserial、Pillow 和可导入的 `vfd_display` API。
随后在 Codex `/hooks` 中信任 `PreToolUse(request_user_input)`、
`PermissionRequest` 和 `Stop`。

## 设备与启动

设备管理器应出现 `USB Serial Device (COMx)`。不要用 Zadig 将 CDC Serial
接口替换成 WinUSB。自动发现要求只有一块 VID:PID `303a:1001` 设备；否则指定
COM 口或 USB 序列号。

```powershell
.\host\run_bridge.ps1 -Mode codex -Verbose
.\host\run_bridge.ps1 -Mode demo -Fps 30
.\host\run_bridge.ps1 -Port COM3 -Mode codex
.\host\run_bridge.ps1 -SerialNumber "D0:CF:13:F0:B7:4C"
```

默认模式是 `codex`，默认刷新率是 30 FPS。`-Demo` 作为旧启动方式的兼容别名
仍等价于 `-Mode demo`。文本使用内置固定 5×7 ASCII 字体，不依赖 Windows 字体。
中文任务会以 `CN ` 开头并保留英文、数字和符号；纯中文摘要显示 `CN TASK`。

Windows App、CLI 和 IDE 默认共享 `%USERPROFILE%\.codex`。WSL 默认使用独立的
Linux `~/.codex`；若需要让 Windows 上位机看到 WSL rollout，可设置：

```bash
export CODEX_HOME=/mnt/c/Users/<windows-user>/.codex
```

## 测试

```powershell
.\host\.venv\Scripts\python.exe -m unittest discover -s host\tests -v
.\host\.venv\Scripts\python.exe host\check_device.py --port COM3
.\host\.venv\Scripts\python.exe host\benchmark_device.py --port COM3
```

上位机、串口监视器、烧录器和硬件检查不能同时打开同一个 COM 口。USB 拔插后
上位机会重新握手并重发最新帧；上位机停止后固件约 2 秒清屏。
