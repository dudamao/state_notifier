# ESP32-C5 40×7 VFD 显示模组

本项目将 `8-MD-06INKM` VFD 作为通用 USB 显示模组使用。ESP32-C5 固件只负责
SN2 USB 通讯、协议反馈和 VFD 点阵写入；文字、动画、Codex 状态、额度与滚动
全部由 macOS/Windows 上位机生成。

SN2 与旧 SN1 状态协议不兼容。升级上位机后必须重新烧录本目录中的固件。

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
cd /Users/duyikang/master/02_dev/state_notifier
source /Users/duyikang/.espressif/tools/activate_idf_v6.0.2.sh
idf.py build
idf.py -p /dev/cu.usbmodem21201 flash
```

USB Serial/JTAG 被 SN2 独占，烧录、monitor、硬件测试和上位机不能同时打开同一
端口。应用日志仍输出到 UART0。

## 安装上位机

macOS：

```sh
./host/setup_macos.sh
/usr/bin/python3 host/install_hooks.py install
```

Windows 见 `WINDOWS.md`。安装 hooks 后在 Codex `/hooks` 中审阅并信任
`PreToolUse(request_user_input)`、`PermissionRequest` 和 `Stop` 三项命令。

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

## Codex 与演示模式

无参数或显式 Codex 模式：

```sh
./host/run_bridge.sh
./host/run_bridge.sh --mode codex --fps 30 --verbose
```

演示模式：

```sh
./host/run_bridge.sh --mode demo --fps 30
```

Codex 模式保留 hooks、根 rollout 状态、额度读取、JSONL 回退和 USB 自动重连。
第一位显示状态图标：运行 `▶`、批准/输入 `⏸`、完成 `⏹`、错误 `×`。只有需要
注意的图标以 500 ms 闪烁；剩余 35×7 像素滚动最近一条根任务用户消息的首句。
摘要跳过文件声明和代码块、清除 Markdown/URL、转为大写 ASCII，最多 64 字符，
且不会读取工具参数、命令输出、助手回复或子代理内容。中文摘要会保留其中已有的
英文、数字和符号并添加 `CN ` 前缀；没有可用 ASCII 信息时显示刚好 7 字符的
`CN TASK`，不再尝试显示汉字。

多任务按批准、输入、错误、完成、运行的优先级选择，同级显示最近活动任务。
完成/错误保持 10 秒；空闲时两个额度窗口每 3 秒轮播，无有效额度显示 `LIMIT--`。

## 测试

```sh
./tests/run_native_tests.sh
host/.venv/bin/python -m unittest discover -s host/tests -v
idf.py build
```

连接并停止上位机后：

```sh
host/.venv/bin/python host/check_device.py --port /dev/cu.usbmodem21201
host/.venv/bin/python host/benchmark_device.py --port /dev/cu.usbmodem21201
```

硬件性能验收为连续 10 分钟发送 30 FPS：平均提交至少 29 FPS，任意 1 秒窗口
至少 24 FPS，CRC/解析错误为零。图标、棋盘格、滚动、亮度和断线约 2 秒清屏仍需
实机目视确认。
