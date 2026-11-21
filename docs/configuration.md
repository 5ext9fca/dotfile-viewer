# 声明语义

项目只维护声明，不维护应用配置内容。`dotfile_viewer/resources/software/items/<id>.toml` 是唯一软件/配置声明来源；文件名 ID 为 `[a-z0-9][a-z0-9-]*`。Python 标准库 TOML parser 解析，不执行声明文本。

仓库提供缺省声明和 profile，用户负责按自己的机器增删或修改软件、安装来源、依赖、配置路径及排除项。缺省值不是完整的软件支持清单或自动配置发现结果；配置专用项也不代表工具会安装该应用。仅在某台 macOS 主机确认的新增路径只声明 macOS，不据此推断 Linux 或 Windows 路径。

选定的每条配置路径都必须存在；未使用的配置应从自己的声明或选择集合中移除，而不是依赖工具静默忽略。单文件声明只包含该文件，目录声明递归包含未来新增的未排除文件。用户须核对配置正文中的凭据、身份和本机绝对路径；即使文件名是 config.toml，也不保证它不含敏感信息。

## 软件声明 v1

```toml
schema_version = 1

[install.macos]
order = "20"
method = "brew"
packages = "fish"
source_kind = "default"
install_depends_on = "homebrew"

[[config]]
id = "settings"
platforms = ["linux.apt", "linux.dnf", "linux.pacman", "macos"]
root = "config_home"
path = "fish"
exclude = ["fish_variables"]
```

安装表支持 `linux.apt`、`linux.dnf`、`linux.pacman`、`macos`、`windows`；有点或斜线的完整平台键需使用引号，例如 `[install."linux.apt"]`、`[install."windows/winget"]`。安装字段值保持字符串，包括 order。字段与机制/来源组合仍由 `dotfile_viewer/resources/install/schema/fields.tsv`、`methods.tsv`、`sources.tsv` 校验。

公共依赖为 `depends_on`；仅安装时需要的依赖为 `install_depends_on`，多安装方式子表使用 `depends_on`。`methods`、`preferred_method`、发现路径、`provides`、`wsl_provider` 保持原有安装所有权语义。安装成功由命令执行策略判定，不进行安装后存在性检查。

配置专用的软件也可声明依赖：

```toml
[requires]
macos = ["xcode-command-line-tools"]
```

`requires` 以安装平台为键，依赖是软件 ID 数组。软件可仅安装、仅配置或两者都有。所有平台的依赖必须存在、无环；安装规则的依赖必须有该平台安装规则。配置专用项可依赖其他软件。

## 配置路径

每个 `[[config]]` 必填 `id`、`platforms`、`root`、`path`，可选 `exclude` 和 `environments`（native/wsl 数组，默认两者）。软件内配置 ID 唯一，允许多个目录或单文件。

- Unix 根：`home`、`config_home`；后者取 XDG_CONFIG_HOME 或 HOME/.config。
- Windows 根：`home`、`app_data`、`local_app_data`。
- path 和 exclude 是 `/` 分隔的相对路径，不接受 `..`、绝对路径、变量替换、控制字符或 Windows 保留的路径字符。
- exclude 数组按精确相对路径及后代匹配，不是 glob；目录中未来新增的未排除文件会自动纳入下一次采集。
- 目录和文件均须存在，实际类型由采集记录。排除项不能让整个声明路径缺失变成成功。
- 不支持 source、method、condition、renderer；不从仓库模板渲染内容，也不把配置硬链接到仓库。

实际配置覆盖只由这些路径决定，声明软件不代表自动发现它的所有配置。桌面字体为显式配置专用项，采集 Fontconfig、KDE、GTK 的真实文件；仅 native 环境适用；未配置该桌面时不要选择该项。

## Profile v3

```ini
schema_version=3
software=fish git helix
```

只有以上两个字段，software 是空格分隔的唯一软件 ID，或者单独的 `all`。默认 profile 选择较小的软件集合；`*-full` 使用 all。依赖自动补齐。all 根据平台及 native/WSL 适用性选择，不适用环境的项目排除；显式选择不适用项目会失败。

安装入口只处理所选项的安装规则。快照创建同时采集依赖闭包内的配置；恢复读取快照中已保存的声明，不重新读取 profile。安装下载镜像开关只影响安装调用，不初始化持久镜像；快照恢复默认使用归档里的来源规则。

## 版本与兼容

软件声明 v1 和 profile v3 保持不变；快照使用 v2，不接受旧 v1。历史 journal 恢复入口已移除，已有资料不删除。安装执行规则由 syncronizer 维护。
