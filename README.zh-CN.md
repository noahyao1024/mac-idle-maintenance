# mac-idle-maintenance

开发者 Mac 的定期维护工具：电脑闲置时关闭旧模拟器，清理可重新生成的缓存。
无需第三方 Python 包或 sudo，默认仅预览。需要 macOS 和 Python 3.9+。

## 安装与使用

```sh
git clone https://github.com/noahyao1024/mac-idle-maintenance.git
cd mac-idle-maintenance

# 先预览
python3 mac-maintenance.py

# 执行一次，必须满足闲置条件
python3 mac-maintenance.py --apply

# 每 6 小时及登录时检查；默认仅预览
python3 install.py

# 显式开启自动清理，安装后立即检查一次
python3 install.py --enable-cleanup
```

默认规则：电脑无键鼠操作至少 15 分钟才修改；关闭 `lastUsedAt` 超过 3 天的已启动模拟器；
npm 下载缓存超过 8 GiB 时清理，成功清理后至少间隔 7 天；删除 30 天未更新的 DerivedData 子目录及旧日志。
检测到 Xcode、Simulator 界面及构建／测试进程时，跳过模拟器、构建缓存和日志清理；包管理器运行时跳过 npm 缓存。
完整 Xcode 会自动查找，没有安装时只跳过模拟器步骤。

**`lastUsedAt` 和键鼠闲置时间是判断线索，不能证明后台没有工作。**
无人值守测试和远程自动化等场景，请把设备 UDID 加入 `excluded_simulator_ids`。
DeviceHub 单独保持打开不会阻止闲置清理。

保留模拟器设备数据、运行时镜像、Xcode Archives、项目、已安装依赖、下载文件、LLM 模型和系统交换文件。
不会停止 Qwen、OpenClaw 或 Multica。缓存删除后，下次安装或构建可能需要重新下载、生成文件。
缓存直接删除，不经过废纸篓；请先查看预览。没有遥测和上报功能。

## 配置与停用

- 配置：`~/Library/Application Support/MacIdleMaintenance/config.json`，参考 [配置示例](config.example.json)。
- 日志：`~/Library/Logs/MacIdleMaintenance/maintenance.log`，轮转后约保留 5 MiB。
- 自定义周期：`python3 install.py --enable-cleanup --interval-hours 12`。
- 仅写入安装文件、不启动计划：安装时加 `--no-start`。
- 停用计划：`python3 install.py --uninstall`，保留脚本、配置和日志。
- 更新：拉取代码后重新运行安装命令，已有配置会保留。

不要与旧的同类定时任务并行安装。计划任务仅在登录期间运行，电脑关机或登出时不执行。
内存压缩和交换空间由 macOS 管理，不保证立即下降。

完整说明见 [English README](README.md)。许可证：[MIT](LICENSE)。
