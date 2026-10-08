# Windows SN2 上位机

## 要求与升级

支持 Windows 10/11、Python 3.10 或更新版本，以及 Espressif USB Serial/JTAG
CDC 设备。SN2 与旧 SN1 不兼容，必须先用新源码重新烧录 USB 变体固件。

默认固件是自对时 WiFi 时钟（USB 仅供电），不响应 SN2；`host/` 上位机需要
`CONFIG_STATE_NOTIFIER_USB_DISPLAY=y` 的 USB 直连显示变体。

## 固件构建与烧录

本机 ESP-IDF 必须包含 ESP32-C5 的 MSPI 时序修复（AR2026-002，ESP-IDF v5.5.4
起）。v5.5.3 及更早版本编译出的二级 bootloader 会在 flash timing tuning 处
`CPU_LOCKUP` 重启，设备表现为串口无响应。

```powershell
. C:\Espressif\tools\Microsoft.v6.0.2.PowerShell_profile.ps1

# 默认构建：WiFi 时钟固件（上电连接热点对时，之后本地走时）
idf.py build
idf.py -p COM3 flash

# USB 直连显示变体（配合 host 上位机与 pytest）
idf.py -B build_usb -DSDKCONFIG="sdkconfig.usb" `
  -DSDKCONFIG_DEFAULTS="sdkconfig.defaults;sdkconfig.usb.defaults" build
idf.py -B build_usb -p COM3 flash
```

WiFi + lwIP 无法装进默认的 1 MB factory 分区，`sdkconfig.defaults` 已切换到
`CONFIG_PARTITION_TABLE_SINGLE_APP_LARGE`（1500 KB，当前固件约 883 KB）。分区
偏移不变，无需 erase-flash。热点 SSID/密码、时区、SNTP 服务器在
`idf.py menuconfig` → "State notifier" 中配置。

USB Serial/JTAG 被 SN2 独占，烧录、monitor 和上位机不能同时打开同一端口。
应用日志输出到 UART0，不经 USB 转发。

`idf.py flash` 若报 `Failed to start stub flasher` 或 `No serial data received`
（USB-Serial/JTAG 偶发，重试通常即可），可直接调用 esptool 跳过 stub：

```powershell
Set-Location .\build
& C:\Espressif\tools\python\v6.0.2\venv\Scripts\python.exe -m esptool --chip esp32c5 `
  --port COM3 --no-stub --before default-reset --after hard-reset write-flash `
  --flash-mode dio --flash-freq 80m --flash-size 2MB 0x2000 bootloader/bootloader.bin `
  0x8000 partition_table/partition-table.bin 0x10000 state_notifier.bin
```

## 安装

```powershell
Set-Location <state_notifier目录>
powershell -ExecutionPolicy Bypass -File .\host\setup_windows.ps1
```

安装器创建 `host\.venv`，安装 pyserial、Pillow 和可导入的 `vfd_display` API。

## 设备与启动

两种固件下设备管理器都会出现 `USB Serial Device (COMx)`（默认固件仅供电，
不回 SN2）。不要用 Zadig 将 CDC Serial 接口替换成 WinUSB。自动发现要求只有
一块 VID:PID `303a:1001` 设备；否则指定 COM 口或 USB 序列号。

```powershell
.\host\run_bridge.ps1
.\host\run_bridge.ps1 -Port COM3
.\host\run_bridge.ps1 -Mode demo -Fps 30
.\host\run_bridge.ps1 -SerialNumber "D0:CF:13:F0:B7:4C"
```

默认模式是 `clock`：`HH:MM:SS` 恰好 8 个字符，占满 8 个字符位，每秒刷新一次。
`-Demo` 等价于 `-Mode demo`，按 25 秒循环显示移动棋盘格、5 张全字符卡片和实时
时钟。文本使用内置固定 5×7 ASCII 字体，不依赖 Windows 字体。

## 测试

固件逻辑原生测试（Git Bash 中运行，覆盖 SN2 编解码、时钟渲染、字库一致性）：

```powershell
sh tests/run_native_tests.sh
```

以下设备测试需要烧录 USB 变体固件：

```powershell
.\host\.venv\Scripts\python.exe -m unittest discover -s host\tests -v
.\host\.venv\Scripts\python.exe host\check_device.py --port COM3
.\host\.venv\Scripts\python.exe host\benchmark_device.py --port COM3
```

上位机、串口监视器、烧录器和硬件检查不能同时打开同一个 COM 口。USB 拔插后
上位机会重新握手并重发最新帧；上位机停止约 2 秒后固件自动恢复显示本机时钟
（不再清屏）。
