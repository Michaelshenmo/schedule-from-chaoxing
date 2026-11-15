<div align="center">
<img src="icon.png" width="15%" alt="Class Widgets 2">
<h1>超星学习通课表同步</h1>

<p>从超星学习通自动拉取课表，并写入独立的 Class Widgets 2 课表</p>

[![星标](https://img.shields.io/github/stars/Michaelshenmo/schedule-from-chaoxing?style=for-the-badge&color=orange&label=%E6%98%9F%E6%A0%87)](https://github.com/Michaelshenmo/schedule-from-chaoxing)
[![开源许可](https://img.shields.io/badge/license-MIT-blue.svg?label=%E5%BC%80%E6%BA%90%E8%AE%B8%E5%8F%AF%E8%AF%81&style=for-the-badge)](https://github.com/Michaelshenmo/schedule-from-chaoxing)
[![下载量](https://img.shields.io/github/downloads/Michaelshenmo/schedule-from-chaoxing/total.svg?label=%E4%B8%8B%E8%BD%BD%E9%87%8F&color=green&style=for-the-badge)](https://github.com/Michaelshenmo/schedule-from-chaoxing)

</div>

## 介绍 / Introduction

本插件基于 [Class Widgets 2 SDK](https://github.com/Class-Widgets/class-widgets-sdk)，通过超星学习通统一登录接口获取当前账号的课表，并生成一个独立的课表配置。

- 使用超星学习通账号自动登录并拉取课表
- 自动将课程、教师、地点、周次和上课时间转换为 Class Widgets 2 课表格式
- 同步结果写入独立课表，不会覆盖或修改你正在使用的课表
- 支持分别开关「同步课程」和「同步时间线」
- 启动 Class Widgets 时自动同步一次
- 支持设置自动同步间隔，默认 20 分钟
- 可分别控制启动、定时和手动同步的通知
- 支持从下载地址获取学校导出的 `.xlsx` 课表，或直接选择本地文件
- 课程与时间线可来自不同数据源：课程来自课表文件/超星，时间线可来自自建服务器
- 时间线服务器地址和访问令牌由用户自行设置
- 按获取到的时间线从上到下填充课程类型日程
- 关闭「同步时间线」时保留已设置的课表时间线，并继续填充已有时间线
- 支持一键切换到目标课表查看同步结果
- 账号密码先验证再加密保存，设置页面不会回显已保存的账号

## 使用 / Usage

1. 安装 [Class Widgets 2 SDK](https://github.com/Class-Widgets/class-widgets-sdk) 依赖：

```bash
pip install class-widgets-sdk
```

2. 在终端运行 `cw-plugin-pack` 打包插件：

```bash
cw-plugin-pack
```

3. 在 Class Widgets 2 的「设置」→「插件」中导入并启用插件。

4. 打开「设置」→「插件」→「超星学习通」：

   - 输入超星学习通手机号/学号和密码，点击「保存并验证」
   - 按需开启「同步课程」和「同步时间线」
   - 设置自动同步间隔（默认 20 分钟）
   - 在「通知选项」中分别控制启动、定时和手动同步通知
   - 如果超星课表接口没有排课数据，可在「课表导出文件」中选择学校导出的 `.xlsx`，或填写课表文件下载地址
   - 在「时间线服务器」中填写 admin-system 提供的时间线接口地址；需要鉴权时填写访问令牌
   - 可修改目标课表名称（默认「超星学习通」），或点击「切换课表」查看结果
   - 点击「立即同步」可手动同步一次

5. 同步完成后，在 Class Widgets 2 的课表设置中切换到目标课表即可。

## 同步说明

- 插件会在课表目录中创建或更新独立课表，不会自动切换你当前正在使用的课表。
- 如果目标课表名称已存在，且不是本插件创建的课表，插件会拒绝覆盖，请更换目标课表名称。
- 目标课表由插件管理，手动修改会在下次同步时被覆盖。
- 「同步课程」负责从超星课程列表和课表写入课程名称、教师、地点和课程颜色。
- 「同步时间线」负责写入上课时间段和周次；如果超星课表本身没有课程时间，则只同步课程并给出提示。
- 关闭「同步时间线」时，插件不会删除目标课表中已有的时间线，并会按已有时间线从上到下依次填充课程类型日程。
- 选择 `.xlsx` 课表文件或填写课表文件下载地址后，插件会优先使用该文件，并按时间线的节次顺序填充课程；开启「同步时间线」但目标课表没有可用时间线时，会使用内置的 20 分钟节次时间。
- 设置时间线服务器后，插件会优先使用服务器返回的节次时间；服务器不可用时会回退到目标课表时间线或内置节次时间，并在同步状态中提示。
- 时间线接口支持常见的 `timeSlots`、`periods`、`days` 等字段，节次可包含 `name`、`start`/`end`、`period`、`dayOfWeek`；具体字段可按 admin-system 返回结构调整。
- 示例：`{"data":{"timeSlots":[{"name":"第一节","start":"08:00","end":"08:40"}]}}`；按星期返回时可用 `{"data":{"days":{"1":[{"name":"第一节","start":"08:00","end":"08:40"}]}}}`。
- 两项都关闭时，自动同步会停止。
- 如果超星学习通账号需要二次验证（如短信验证码），当前版本无法自动同步。

## 账号安全

- 账号密码不会以明文写入配置文件。
- Windows 下优先使用 DPAPI 加密，密文只能由当前 Windows 用户解密。
- 非 Windows 平台使用随机密钥加密，并校验数据完整性。
- 设置页面不会读取或显示已保存的账号；重新设置时只能输入新的账号密码。
- 插件不会上传账号信息到任何第三方服务器，仅用于登录超星学习通。

## 致谢 / Acknowledgements

- [Class Widgets 2](https://github.com/rinlit-233-shiroko/class-widgets-2)
- [Class Widgets 2 SDK](https://github.com/Class-Widgets/class-widgets-sdk)

## 版权 / License

本项目基于 MIT 协议开源，详情请参阅 [LICENSE](LICENSE) 文件。
