# ESP32-C5 40×7 VFD 显示模组

本项目把 `8-MD-06INKM` VFD 做成一台上电自动对时的桌面时钟：ESP32-C5 固件启动
后连接指定热点、通过 SNTP 获取时间，随后关闭 WiFi 射频，只靠芯片内部硬件定时
器走时，并在本地用内置 5×7 点阵字体渲染 `HH:MM:SS`。USB 仅作供电。

固件同时保留 SN2 USB 直连显示端点（由上位机推送任意 40×7 点阵，时钟、文字与
动画全部由 macOS/Windows 上位机生成），但默认关闭，需要用
`sdkconfig.usb.defaults` 构建变体。SN2 与旧 SN1 状态协议不兼容，升级上位机后
必须重新烧录 USB 变体固件。

## 硬件与点阵

| VFD 信号 | ESP32-C5 | 说明 |
| --- | --- | --- |
| MOSI | GPIO28 | SPI2 数据；同时是启动绑带脚 |
| SCLK | GPIO26 | SPI2 时钟，500 kHz |
| CS | GPIO23 | SPI2 片选 |
| RST | GPIO24 | 低 100 us、高 200 us 复位 |
| EN | 外部拉高 | 固件不主动驱动 |

原生 USB D-/D+ 使用 GPIO13/GPIO14。第一次烧录后必须断电重启，确认 VFD 输入
没有在复位期间错误拉动 GPIO28。

显示逻辑尺寸是 40×7：8 个物理字符位，每位 5×7。每列用一个字节表示，bit 0
是最上方像素，bit 6 是最下方像素。物理字符位之间的间隙不能由软件消除。

## WiFi 对时与本地走时（默认固件）

上电后的 UART0 日志时间线：

```text
nvs ok -> display ready -> scheduler ready -> network stack ready ->
joining '<SSID>' -> got ip 192.168.x.x -> system time set: 2026-.. ->
wifi released; clock runs on the internal timer
```

- 连接参数在 `idf.py menuconfig` → "State notifier" 中配置：SSID/密码、连接与
  SNTP 超时、SNTP 服务器（默认 `ntp.aliyun.com`）、时区（POSIX TZ 字符串，
  默认 `CST-8` 即 UTC+8）、重试退避上下限、亮度。
- 同步成功前显示 `--:--:--`；热点不在或同步失败时按 5/10/20/40/60 秒退避持续
  重试，中途开启热点无需重启，成功即上屏。
- 同步成功后立即关闭 WiFi 射频（`esp_wifi_deinit` 关闭调制解调器电源域），
  之后没有任何网络访问：系统时间由内部硬件定时器（systimer/RTC）维持，
  1 Hz 硬件节拍按整秒边界刷新显示。
- 设备无备用电池，掉电重启后时间丢失、需重新同步；单次同步后晶振漂移约
  1–3 s/天。若在意精度，可通过扩展接口加默认关闭的定期重同步。
- 扩展接口（为"通过互联网显示其他信息"预留）：`main/content_source.h` 是内容
  源抽象（`render()` 产出 40×7 帧，时钟是内置实现）；`main/net_session.h` 是
  可复用的网络会话（`acquire()` 拉起 WiFi 并等 DHCP，`release()` 关闭射频）。
  新增一个联网内容源 = 实现 `ContentSource`，在它的 worker 任务里
  `net_session::acquire()` → 抓取数据 → `release()` → 组帧缓存 →
  `scheduler::notify()`；多个内容源轮换写一个持有子源的 `ContentSource` 即可，
  `scheduler` 无需改动。

## SN2 协议

每个包先按以下结构生成，再做 COBS 编码并追加 `00` 分隔符：

```text
"SN" | version=2 | type | sequence:u16le | length:u16le | payload | crc16-ccitt:u16le
```

最大负载为 64 字节。消息类型：

| 请求 | 值 | 负载 |
| --- | ---: | --- |
| HELLO | `01` | 空 |
| FRAME | `02` | 40 个点阵列字节 |
| CLEAR | `03` | 空 |
| SET_BRIGHTNESS | `04` | 0–255 |
| PING | `05` | 空 |
| CAPABILITIES | `81` | `8,5,7,40,60,feature_bits` |
| ACK / NACK | `82` / `83` | 结果码及累计提交帧数 |
| PONG | `85` | 累计提交帧数 |

合法帧以“命令 + 5 个点阵字节”的精确长度写入 8 个 CGRAM 槽，不能追加填充
字节；8 个槽全部写完后只执行一次显示提交。连续 2 秒没有合法 SN2 包时固件
清屏；上位机即使显示静态内容也会每秒发送 PING。

## 构建与烧录

```sh
# 默认构建：WiFi 时钟固件（USB 仅供电）
idf.py build
idf.py -p <PORT> flash

# USB 直连显示变体（配合 host 上位机与 pytest）
idf.py -B build_usb -DSDKCONFIG="sdkconfig.usb" \
  -DSDKCONFIG_DEFAULTS="sdkconfig.defaults;sdkconfig.usb.defaults" build
idf.py -B build_usb -p <PORT> flash
```

macOS 构建前先激活 IDF（`source .../activate_idf_v6.0.2.sh`），Windows 见
`WINDOWS.md`。

WiFi + lwIP + wpa_supplicant 无法装进默认的 1 MB factory 分区，
`sdkconfig.defaults` 已切换为 `CONFIG_PARTITION_TABLE_SINGLE_APP_LARGE`
（1500 KB 分区，当前固件约 883 KB）。分区偏移不变，无需 erase-flash。

USB 变体下 USB Serial/JTAG 被 SN2 独占，烧录、monitor、硬件测试和上位机不能
同时打开同一端口。两种固件的应用日志都输出到 UART0；默认固件不安装 USB 驱动。

## 安装上位机

macOS：

```sh
./host/setup_macos.sh
```

Windows 见 `WINDOWS.md`。

## Python API

安装后可在任意 Python 脚本中导入：

```python
from vfd_display import Frame40x7, TextRenderer, VfdClient

rows = [[(x + y) % 2 == 0 for x in range(40)] for y in range(7)]
checkerboard = Frame40x7.from_rows(rows)

with VfdClient() as display:
    display.set_brightness(220)
    display.show(checkerboard)
```

`Frame40x7` 还提供 `from_image()`、`crop()`、`translated()`、`overlay()` 和
`with_bitmap()`。`TextRenderer()` 使用内置的固定 5×7 点阵表，每个英文大写字母、
数字和常用 ASCII 符号都严格占 5 列；小写自动转大写，不支持的字符显示 `?`。
文本不再加载或缩放系统字体，Pillow 仅供 `from_image()` 栅格化图片使用。动画使用
容量为 1 的 latest-wins 队列：

```python
with VfdClient() as display:
    frames = [Frame40x7((value,) * 40) for value in range(128)]
    display.play(frames, fps=30, loop=True)
```

`fps` 范围为 1–60。ACK 在后台接收，不会逐帧阻塞；`display.stats` 可读取已发送、
已确认、丢弃、NACK、重连和设备累计提交帧数。

## 上位机时钟与自检模式（仅 USB 变体）

以下命令需要 USB 直连显示变体固件。默认（无参数）显示上位机系统时钟：

```sh
./host/run_bridge.sh
./host/run_bridge.sh --mode clock --fps 30 --verbose
```

`HH:MM:SS` 恰好 8 个字符，内置 5×7 字体每个字符严格占 5 列，正好铺满 40 列，
不滚动也不裁切。时钟每秒变化一次，上位机只在点阵内容改变时发送 FRAME，其余
时间以每秒一次的 PING 维持连接。上位机活跃期间本机时钟暂停上屏；停止上位机
约 2 秒后固件自动恢复显示本机时钟（不再清屏）。

自检模式按 25 秒循环依次显示移动棋盘格、5 张全字符卡片（`ABCDEFGH`、
`IJKLMNOP`、`QRSTUVWX`、`YZ012345`、`6789!?+-`）和实时时钟，用于烧录后的
硬件目视自检：

```sh
./host/run_bridge.sh --mode demo --fps 30
```

## 测试

```sh
./tests/run_native_tests.sh
host/.venv/bin/python -m unittest discover -s host/tests -v
idf.py build
```

`run_native_tests.sh` 覆盖 SN2 编解码、时钟文本格式化与 5×7 渲染，并逐字节
校验固件字库与上位机 `host/vfd_display/font5x7.py` 一致。

连接设备并烧录 USB 变体后（停止上位机）：

```sh
host/.venv/bin/python host/check_device.py --port /dev/cu.usbmodem21201
host/.venv/bin/python host/benchmark_device.py --port /dev/cu.usbmodem21201
```

`pytest_state_notifier.py`（pytest-embedded）验证 SN2 协议交互，同样需要 USB
变体固件与设备串口（`--embedded-services esp,serial`）。

硬件性能验收为连续 10 分钟发送 30 FPS：平均提交至少 29 FPS，任意 1 秒窗口
至少 24 FPS，CRC/解析错误为零。默认（时钟）固件上 SN2 端点未启用，串口测试
必须使用 USB 变体。时钟、棋盘格、自检字符卡、亮度、WiFi 对时时间线和"停止
上位机约 2 秒后恢复本机时钟"仍需实机目视确认。
