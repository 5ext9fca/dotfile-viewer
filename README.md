# Dotfile Viewer

按软件声明集中查看、编辑本机配置，并导出和应用独立快照。项目维护软件声明，不维护应用配置正文；当前内容以本机实际文件为准。

`dotfile-syncronizer` 依赖本项目的 Python API，消费同一份声明，负责软件安装和“先安装、后应用配置”的组合流程。Viewer 自身不安装软件。

## 准备与启动

先安装 Python ≥3.14；项目验证使用 Python 3.14。运行前安装包和依赖，不在命令执行期间自举环境。开发 checkout 可执行：

```bash
git clone https://github.com/5ext9fca/dotfile-viewer.git
cd dotfile-viewer
python3.14 -m venv .venv
.venv/bin/python -m pip install -e .
./dotfile-viewer list --json
```

根目录的 `./dotfile-viewer` 默认使用项目的 `.venv/bin/python`，也可用 `DOTFILES_PYTHON` 指定已安装本项目的解释器。Windows 在项目根目录运行 `.venv\Scripts\python.exe main.py list --json`。

Windows 使用 `.venv\Scripts\python.exe` 和 `.venv\Scripts\dotfile-viewer.exe`。项目通过 CLI 和 Python API 提供能力，无第三方运行时依赖。

Python API 当前为 v3，安装器需同步迁移快照会话接口。Python API 使用 `dotfile_viewer.api`，模块入口为 `python -m dotfile_viewer`。从旧名称升级时先卸载 `dotfiles-viewer`，再安装本项目，并同步升级 syncronizer；不提供旧名称兼容入口。详见[更名迁移](docs/migration.md)。

## 配置编辑

```bash
dotfile-viewer files fish settings
dotfile-viewer show fish settings config.fish --json
EDITOR=hx dotfile-viewer edit fish settings config.fish
# VERSION 来自 show --json；修改期间文件发生变化会拒绝覆盖
dotfile-viewer write fish settings config.fish --from candidate.fish --expected-version VERSION
```

缺失配置显示状态，不从仓库补齐。单文件声明省略最后的相对文件参数。支持 UTF-8（含 BOM），保留原换行；编辑上限 2 MiB。二进制、其他编码、混合换行及链接不通过文本编辑器修改。目录排除项不展示、不编辑。

脚本命令 `write` 先从文件或标准输入读完候选文本并解码，再检查目标和预期版本。保存前备份并检查文件版本。多硬链接目标需 `--detach-hardlink` 明确确认。外部编辑器成功退出后，如果草稿读取失败、内容无效或保存冲突，CLI 会显示保留草稿的临时目录。可从中取回编辑器写出的原始文件。编辑器非零退出会取消保存。

## 快照

```bash
dotfile-viewer snapshot export --profile macos --output /existing/path/settings.zip --dry-run --json
dotfile-viewer snapshot export --software fish helix --output /existing/path/settings.zip
dotfile-viewer snapshot inspect --snapshot /existing/path/settings.zip
dotfile-viewer snapshot apply --snapshot /existing/path/settings.zip --dry-run --json
# 将 VERSION 替换为上一步输出的顶层 version
dotfile-viewer snapshot apply --snapshot /existing/path/settings.zip --expected-version VERSION
```

`snapshot export` 成功时默认静默，包括 `--dry-run`。添加 `--json` 可将快照清单打印到标准输出。

快照包含所选软件及依赖的声明、普通文件和空目录；二进制文件不受编辑器的文本限制。采集前停止会写配置的应用。缺失、不可读、未排除链接及检测到的采集变化会导致失败。输出不覆盖已有文件。

可选的 `--expected-version` 用于核对目标应用计划。版本不匹配时，命令在写入前退出（退出码 4），请重新预览。两次 CLI 调用各自读取归档，请使用同一份快照。

仅支持新 v2 ZIP；旧 v1 需要重新导出。应用要求安装分支、CPU 架构和 native/WSL 环境一致。相同内容跳过，冲突先备份，保留目标额外文件，不安装软件。

备份默认位于 Unix `${XDG_STATE_HOME:-$HOME/.local/state}/dotfiles-viewer/backups`，Windows `%LOCALAPPDATA%/dotfiles-viewer/backups`。可通过 `DOTFILES_BACKUP_DIR` 指定备份目录。每次写入记录原文件、目标路径和结果，失败时查看这些资料并人工处理后重跑。没有历史 journal 恢复命令，也不自动回收异常退出留下的部署锁。

## 统一声明

缺省声明随包分发；源文件位于 `src/resources/software/items/`，profile 位于 `src/resources/workflow/profiles/`。不保存配置正文。

需要单独维护声明时，准备包含 `software/items/` 和 `workflow/profiles/` 的目录：

```bash
dotfile-viewer --catalog /path/to/catalog list
# syncronizer 同样支持 --catalog DIR，或共享 DOTFILES_CATALOG 环境变量
```

显式 `--catalog` 优先于 `DOTFILES_CATALOG`，目录整体替代默认声明，不合并。现有软件声明 v1、profile v3 和依赖选择语义保持不变。

详见 [声明语义](docs/configuration.md)、[架构和 API](docs/architecture.md)、[迁移说明](docs/migration.md)、[测试](tests/README.md)。

本项目采用 [MIT 许可证](LICENSE)。
