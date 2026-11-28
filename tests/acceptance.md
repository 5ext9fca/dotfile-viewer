# 验证记录

## 2026-11-27 · 三系统默认安装 Tailscale

Fedora 45 KDE、原生 x86_64、Python 3.14.10。用 `api.load_catalog()` 校验声明，再用 `api.selection()` 和 `api.installation_roots()` 检查 APT、DNF、Pacman、macOS、Windows 的默认及完整 profile 均选中 Tailscale，三条 Linux 分支的 WSL 默认及完整 profile 均排除它。

Viewer checkout 缺少自身的 .venv，首次严格检查未启动。将 DOTFILES_PYTHON 指向安装器已有的 Python 3.14 后重跑 `./tests/check.sh --strict`，24 项通过。在安装器运行 `./install/linux.sh --dry-run --yes` 和追加 `--profile linux-full` 的预览，沙箱内均在 install-state 查询失败；原生环境下重跑均成功，两次都发现 /usr/bin/tailscale 并跳过安装。未执行真实安装、登录或其他系统的原生预览。

## 2026-11-22 · Windows 桌面字体声明

原生 Windows 11 Pro x64（26300），Python 3.14.0。使用 syncronizer checkout 的虚拟环境和本仓库的 editable 安装。

- `api.load_catalog()` 通过全平台图校验。Linux 和 Windows 两个字体选择测试通过，验证 Windows 默认、完整和字体专用 profile 的选择，并逐项比较 Windows 与 Linux 的字体来源、文件和家族。
- `tests/check.ps1 -Strict` 运行 25 项，报告 6 个断言失败、9 个错误及 2 项跳过。对修改前 HEAD 单独导出的源码重跑，24 项中也报告 5 个断言失败、9 个错误及 2 项跳过，涉及 Windows 文件状态、快照和符号链接。未运行其他原生平台。
- 已复核两仓库的 README、AGENTS、配置语义和安装说明。Windows 的机器级 GDI 设置由操作者单独应用，Viewer 的归档格式保持原有的文件语义。

## 2026-11-22 · 原生 Linux 界面字体

Fedora 45 KDE、x86_64、Python 3.14.10。使用安装器 checkout 的 Python 通过 editable 安装本仓库。运行 `DOTFILES_PYTHON=/path/to/.venv/bin/python ./tests/check.sh --strict`，24 项通过。

- `api.load_catalog()` 校验全平台依赖图；检查 APT、DNF 和 Pacman 的默认、完整、字体专用 profile 及桌面配置依赖，两款字体均已选中。WSL 的默认、完整 profile 和 macOS、Windows 的完整选择均排除两项。
- 使用临时 HOME 和四个合成配置文件执行 `snapshot export --software desktop-fonts`、`snapshot inspect`、`snapshot apply --dry-run` 和两次应用。归档含两项字体声明及四个配置文件，恢复内容一致，第二次应用全部跳过。
- 在 syncronizer 运行 Fedora 默认、完整和字体专用 profile 的 dry-run。未执行原生 APT、Pacman、WSL、macOS 或 Windows 安装。

## 2026-11-21 · GitHub 公开首版

macOS ARM64，Python 3.14.10。工作区与从暂存源码导出的副本都通过了 `./tests/check.sh --strict`，各 23 项。用合成配置验证了以下行为。

- 外部编辑器产生非法 UTF-8 或遇到保存冲突时，CLI 分别返回 3 或 4，保留草稿的原始字节。
- 逆序排列合法 v2 清单，预览和应用均先处理父目录。首次应用成功，再次应用跳过相同内容并保留额外文件。遍历路径、重复或大小写冲突的归档成员、错误摘要和特殊文件类型均被拒绝。
- 在原子替换后的首次读取注入错误，已有文件恢复备份，本次新建的文件撤回。目标被外部改写、替换成内容相同的新文件或持续不可读时，失败报告记录人工检查状态并保留现场。注入最终报告写入失败后也能恢复原文件。
- 从导出的源码离线构建并安装 wheel，在全新的 Python 3.14 环境、checkout 之外验证 CLI 和模块入口输出一致，公共 API v1 可导入。wheel 的 84 个包内文件与受跟踪源码逐字节一致，54 个软件声明、8 个 profile 和 MIT 许可均已校验，无第三方运行时依赖。

以下 11 月 16 日记录中的前三项故障已在本轮修复，第五项所列归档和故障路径已补测。预览版本确认仍由 Python API 的 expected_version 参数提供。本轮未执行原生 Linux、Windows 或 WSL 验证，也未重跑 syncronizer 集成测试。

## 2026-11-16 · 更名为 dotfile-viewer 与功能核对

环境：macOS ARM64，Python 3.14.10。发行包与 CLI 改为 `dotfile-viewer`，实现包及公共 API 改为 `dotfile_viewer`；同步修改 syncronizer 的依赖、Python 导入、Bash/PowerShell 调用和文档，不保留旧名称代码兼容层。既有备份目录、journal、`DOTFILES_*` 环境变量及共享锁路径不变。

- 卸载旧发行包，离线安装新包后运行 `./tests/check.sh --strict`：18 项隔离测试通过。
- 从干净暂存源码构建 wheel，在全新 Python 3.14 环境中离线安装，并在 checkout 外使用 `python -I` 验证：新 CLI、模块入口和公共 API 可用；旧导入和旧 CLI 不存在；无第三方运行时依赖。
- wheel 中实现和资源与源码逐文件一致；54 个默认声明、五个平台依赖图、8 个 profile、schema 和 Bash/PowerShell 锁适配器检查通过。声明内容和 schema 版本没有变化。
- syncronizer 首次可编辑安装因环境缺少 setuptools 失败；改用已构建的本地 wheel 离线安装成功。`./tests/check.sh --strict`：30 项检查通过、0 失败、24 项按平台跳过；其中 Python 核心测试 163 项，162 通过、1 项因 PowerShell 不可用跳过。包含安装/配置组合快照与共享锁验证，详情见 syncronizer 的 `tests/acceptance.md`。
- 两项目现有虚拟环境均确认新发行包/API 可用、旧导入不存在；9 个相关 Bash 脚本语法及两仓库 `git diff --check` 通过。当前环境未提供 ShellCheck 或 PowerShell。
- syncronizer macOS dry-run：沙箱内 4 项因默认备份父目录的可写性预检受限而失败；在沙箱外重跑后 4 项全部通过、4 项 Linux 检查按平台跳过，未安装软件或应用配置。
- README、迁移、架构、声明编写说明和两项目 AGENTS.md 已按新名称复核；历史验收记录保留当时名称。syncronizer README 遗留的 Web UI 描述同步移除。

### 当前功能与待修复项

核心能力均有实现：声明/profile 加载与选择、本机配置查看和文本编辑、快照 v2 导出/校验/应用、备份与原子发布、共享部署锁。没有发现显式 TODO 或 NotImplemented 功能占位。下列问题通过本轮临时合成夹具复现，属于既有实现缺口，本次更名没有修改其行为：

1. **外部编辑器草稿保护**（`dotfile_viewer/cli.py:run`）：模拟编辑器成功退出但写入非 UTF-8 字节，`file.read_text` 抛错后 TemporaryDirectory 删除草稿；保存失败处理只覆盖后续 `editor.save`。需将草稿读取/校验失败纳入保留流程并测试。
2. **快照条目顺序**（`dotfile_viewer/snapshot.py:Snapshot.parse`、`dotfile_viewer/application.py:SnapshotSession.apply`）：反转合法 manifest 的 entries，归档验证通过；应用到空目录时子条目先创建父目录，后面的父目录条目报 `Target appeared`，已有文件落盘。需在应用前规范化父子顺序，或在验证阶段拒绝不支持的顺序，避免由归档排列引发部分应用。
3. **发布后读取失败的恢复与记录**（`dotfile_viewer/files.py:publish_file`）：在 `os.replace` 成功后的首次 `observe(target)` 注入读取失败，`published` 仍为空，恢复分支未执行；目标保持新内容，原始备份仍在，报告停留在 prepared。需补充能确认本次输出身份时的安全恢复，以及无法确认时的明确失败记录；不得覆盖外部写入。
4. **CLI 预览版本确认**：Python API 已支持 `SnapshotSession.apply(expected_version=...)`，CLI `snapshot apply` 尚未暴露该参数。若需要将两次 CLI 调用的预览与应用关联，可补充参数透传及冲突测试；这不提供跨调用归档固定保证。
5. **验收覆盖**：现有测试有冲突、硬链接、排除、备份失败、替换失败和共享锁用例，但恶意归档覆盖主要只有旧版本拒绝；需扩展路径穿越、重复/大小写冲突成员、哈希损坏、恶意类型及上述三个故障路径的自动化测试。

Web UI、安装器、旧 v1 快照和历史 journal 恢复不是待实现功能，均明确排除在项目范围之外。未运行原生 Linux、Windows、WSL 验证；平台图检查不代表原生覆盖。未采集或修改真实用户配置。

## 2026-11-16 · 移除 Web UI

环境：macOS ARM64，Python 3.14.10。按用户要求移除 Web 服务、静态资源、`serve` 命令及 Web 专用运行时和测试依赖；CLI、公共 Python API 和包内声明保留。以下旧迁移记录仅描述当时状态。

- 构建 wheel，并在全新的 Python 3.14 虚拟环境中离线安装；环境不含 FastAPI、Pydantic、AnyIO、Uvicorn 或 HTTPX。
- 使用该环境设置 `DOTFILES_PYTHON`，运行 `./tests/check.sh --strict`：18 项隔离测试全部通过，覆盖配置编辑、CLI 写入与冲突、备份和替换失败、硬链接、排除与链接、快照往返、预览冲突、归档固定及部署锁。删除 3 项 Web 专用测试，保留原先位于 Web 测试类中的 CLI 写入测试。
- wheel 内容检查通过：无 `web.py` 或静态资源，无第三方依赖或测试 extra；软件声明、profile、schema 和共享锁适配器齐全。在 checkout 外通过隔离导入验证公共 API 和默认声明可用；安装后的 CLI 帮助不再包含 `serve`，调用该命令返回参数错误（退出码 2）。
- README、架构、迁移、测试说明和 AGENTS.md 已同步；`git diff --check` 通过。

未运行原生 Linux、Windows、WSL 检查；未采集或修改真实用户配置。本轮未重新运行 syncronizer 集成测试。

## 2026-11-16 · Viewer 迁移与 Python 3.14

环境：macOS ARM64，Python 3.14.10，FastAPI 0.163.1、Pydantic 2.14.3、AnyIO 4.16.0。两个 checkout 的旧 3.15 虚拟环境移到临时目录，重新创建 3.14 环境；没有修改受管理软件安装项或本机配置。

- `./tests/check.sh --strict`：21 项隔离测试通过，覆盖目录/profile、编辑和脚本写入、版本冲突、换行、备份失败、替换失败、硬链接、排除与链接、快照 v2、归档固定、应用幂等、Web API 身份校验及快照流程。
- `node --check dotfiles_viewer/static/app.js`、Bash 入口语法、Python compileall、`git diff --check`：通过。
- 五个平台的统一声明及依赖图解析通过：linux.apt、linux.dnf、linux.pacman、macos、windows。未据此声明 Linux/Windows 原生执行通过。
- Viewer wheel 已构建并检查统一声明、profile、schema、共享锁适配器和静态资源；不含配置正文或旧安装核心。
- syncronizer 集成：macOS 默认安装 dry-run 通过；24 项快照测试通过，包含伪 Homebrew 安装失败阻断配置和成功后应用；运行时测试 10 项，9 通过，PowerShell 不可用而跳过。完整软件集合的 macOS 上游源 dry-run 同样通过。

初次验证使用原有 Python 3.15 alpha，AnyIO sentinel 和 Pydantic 二进制 ABI 导致 Web 导入失败；按用户后续要求迁至 3.14，恢复 FastAPI/Pydantic/AnyIO 后通过。初次 syncronizer 回归还发现旧版本测试值、配置阶段 sidecar 格式和声明 schema 路径，均已修正并重跑相关测试。

浏览器控制服务不可用，未完成实际浏览器视觉验收；隔离本机预览服务已关闭。Web UI 按用户后续要求保留但不再是必需交付项。本轮未运行原生 Linux、Windows、WSL 检查；未安装真实软件、未采集真实用户配置，也未执行实际本机配置应用。

最终联动验收：syncronizer 完整 core 调用初次为 28 个检查通过、2 个失败、24 个因平台不适用跳过。失败项是旧会话提示断言，以及运行期间更新过的备份目录断言；修正后 `tests/execution-contract.sh` 重跑通过，最终 Python 核心测试 163 项（162 通过、PowerShell 不可用跳过 1 项）。因此适用检查均已有通过记录，未将初次完整调用描述为全绿。

最终 wheel 与 CLI、应用及共享锁源码逐字节一致；从临时目录解包并在隔离 Python 导入路径下加载，成功读取 54 个默认软件声明，确认不依赖源 checkout。共享锁元数据读取失败时不留下锁目录的附加检查通过。文档复核包括两项目 README、AGENTS、架构、配置、迁移和测试说明；旧路径及旧恢复入口提示已更新。

## 2026-11-27：Unix 默认 shell 切换为 zsh

macOS ARM64，Python 3.14.10。用相邻 syncronizer 项目的 Python 运行 `./tests/check.sh --strict`，初次 23 项通过；增加 profile 与快照用例后，25 项全部通过。使用合成 HOME 验证 macOS zsh 的五项采集路径、应用幂等性及未采集的历史、会话和生成文件。测试通过公共 API 读取完整默认 catalog 和普通、完整 Unix profile。同步器使用修改后的 catalog 对两个 macOS profile 执行 dry-run 成功；未做原生 Linux、WSL、Windows 安装。已复核 README、配置指南和 AGENTS.md。

## Unix 交互工具与配置采集（2026-11-27）

macOS ARM64，Python 3.14。通过 DOTFILES_PYTHON 指向现有解释器运行 `./tests/check.sh --strict`，25 个用例全部通过。三个 Unix 普通 profile 均包含 Atuin、Starship、zoxide 和 fzf。合成 HOME 验证 macOS 配置往返与重复应用，生成脚本、历史及密钥未被采集。未运行 Linux、WSL 或 Windows 原生安装。已复核 README、声明指南、配置文档与 AGENTS.md。

## 2026-11-27 · 合并 Unix 工具与桌面字体默认选择

macOS ARM64、Python 3.14。合并后运行 `./tests/check.sh --strict`（通过 DOTFILES_PYTHON 指定解释器），27 项通过，包含 Unix 工具及 Linux、Windows 字体选择。未在 Linux 或 Windows 原生运行。

## 2026-11-27 · 根目录 CLI 与 src 布局

macOS ARM64、Python 3.14。可编辑安装后运行 `./tests/check.sh --strict`，27 项全部通过。从仓库外调用根目录入口和安装后的命令，两者的 `list --json` 输出逐字节一致。公共 API 从 `src/dotfile_viewer/api.py` 加载。本轮未在 Linux、WSL 或 Windows 原生运行。

## 2026-11-27 · 源码直接放在 src

macOS ARM64、Python 3.14.10。重新可编辑安装后，`./tests/check.sh --strict` 的 27 项测试全部通过。另将 wheel 离线安装到全新环境，在仓库外通过 `dotfile_viewer.api` 加载默认声明。wheel 命令与项目根目录入口的 `list --json` 输出逐字节一致。本轮未在 Linux、WSL 或 Windows 原生验证。

## 2026-11-27 · 导出默认静默

macOS ARM64、Python 3.14。运行 `./tests/check.sh --strict`，28 项全部通过。新增用例验证实际导出和预览在默认模式下标准输出为空，指定 `--json` 时输出清单，仅实际导出创建归档。本轮未在 Linux、WSL 或 Windows 原生验证。

## 2026-11-27 · 合并 Tailscale 默认选择与 src 布局

Linux x86_64、Python 3.14.10。可编辑安装后，旧包目录残留的 `__pycache__` 导致 Python 从旧路径查找资源。移走仅含缓存的旧目录后，`./tests/check.sh --strict` 的 28 项测试全部通过。新路径下的 Tailscale 声明与迁移前的 Git blob 一致。本轮未在 macOS、Windows 或 WSL 原生运行。

## 2026-11-29 · eza 默认选择

Fedora 45 x86_64、Python 3.14.10。运行 `./tests/check.sh --strict`，28 项通过。通过公共 API 加载内置声明，校验三个 Linux 包管理器的 native 和 WSL 选择，以及 macOS、Windows 选择，普通和完整 profile 均包含 eza。syncronizer 使用更新后的 Viewer 运行 Linux 普通 profile 的 dry-run，DNF 将 eza 识别为已安装。未实际安装软件，未在 APT、Pacman、WSL、macOS 或 Windows 原生运行。

## 2026-11-29 · 开发工具普通 profile 和桌面配置

Fedora 45 x86_64、Python 3.14.10。公共 API 加载内置声明并解析 APT、DNF、Pacman 各自的 native/WSL 普通 profile，以及 macOS、Windows 普通 profile。严格检查 29 项通过；新增用例在独立临时目录导出和应用合成 Zsh、Fish、Atuin、Starship、Toolbox 设置，核对历史、凭据文件和 IDE 安装目录未被采集。syncronizer 的 Fedora 普通 profile dry-run 成功，列出 OpenJDK、Ruby、.NET SDK 和 TeX Live 的 DNF 命令。未实际安装软件，未在 APT、Pacman、WSL、macOS 或 Windows 原生运行。

## 2026-11-29 · Windows TeX Live ISO

Fedora 45 x86_64、Python 3.14.10。Viewer 严格检查 29 项通过。syncronizer 使用当前 Viewer 验证 Windows 普通与完整 profile 的离线计划，两种 profile 都选择 texlive-iso，默认和中国镜像模式分别解析到 CTAN、清华的 ISO 地址。未在 Windows 原生下载安装。

## 2026-11-29 · CLI 快照应用计划版本校验

Fedora 45 x86_64、Python 3.14.10。运行 `./tests/check.sh --strict`，30 项通过。新增 CLI 用例使用临时目录和合成配置，验证预览版本匹配时应用成功，目标变化时返回 4、保留目标内容且未创建备份会话，显式空值返回 2。本轮未在 macOS、Windows 或 WSL 原生运行。

## 2026-11-29 · 编辑器定位、路径规则与声明校验

Fedora 45 x86_64、Python 3.14.10。三步重构后分别运行 `./tests/check.sh --strict`，依次通过 32、33、34 项。临时 catalog 用例验证单项读取只探测所选配置，实际保存只加载一次 catalog，备份目录与另一配置重叠时拒绝写入。修改声明后，同一编辑器的下一次读取使用新声明。

目录浏览、单项读取、快照导出和清单解析均按完整路径段排除子树。一次 catalog 加载和一次多软件清单解析各构造一份 `InstallSchema`，并覆盖依赖顺序、native/WSL 筛选及依赖缺失的错误。本轮未在 macOS、Windows 或 WSL 原生运行。

## 2026-11-29 · 快照导出索引与应用预检

Fedora 45 x86_64、Python 3.14.10。两步分别运行 `./tests/check.sh --strict`，36 项和 38 项全部通过。1 项和 8 项配置的导出预览均构建两次索引，8 项配置的原基线为 20 次。两次采集之间新增文件或改指根路径均会阻止 ZIP 发布。

8 个目标的 CLI 和 API 应用预览均执行 8 次目标观测，原基线为 16 次。实际应用在部署锁内生成计划，显式会话在构造时拒绝类型冲突。硬链接确认和过期版本检查均在创建备份会话前生效。本轮未在 macOS、Windows 或 WSL 原生运行。

## 2026-11-29 · 编辑器单次读取

Linux x86_64、Python 3.14.10，运行 `./tests/check.sh --strict`，40 项通过。使用临时配置验证空文件、带 BOM 的 CRLF 文本、2 MiB 及超限一字节的文件均只打开一次，版本与 `observe()` 一致。读取结束时原地改写或同名替换目标，读取和保存均报告冲突，外部内容保留，未创建备份会话。本轮未在 macOS、Windows 或 WSL 原生运行。

## 2026-11-29 · 快照目标路径重叠检查

Linux x86_64、Python 3.14.10，运行 `./tests/check.sh --strict`，42 项通过。新增用例覆盖重复目标、Unicode 大小写折叠、两种顺序的父子冲突和名称前缀相似的兄弟路径。不同逻辑根映射到相同目标时，导出在发布归档前报错，应用预览在创建目标或备份目录前报错。本轮未在 macOS、Windows 或 WSL 原生运行。

以临时目录为 home，测量 `_targets()` 解析互不重叠的 `group/item-{i}` 路径的耗时，各运行五次取中位数。64、256、1,024 个目标原先分别为 1.81、14.29、163.35 毫秒，改动后分别为 1.44、6.30、26.12 毫秒。

## 2026-11-29 · 安装声明依赖排序

Linux x86_64、Python 3.14.10，运行 `./tests/check.sh --strict`，44 项通过。新增测试覆盖新就绪条目的数值优先级、同序 ID 排序、菱形依赖、缺失依赖、环境不兼容、WSL provider 和环。逐项比对重构前后五个内置平台的 `api.installation()` 输出，字段与顺序一致。本轮未在 macOS、Windows 或 WSL 原生运行。

合成 linux.apt 目录按 64、256、1,024 条声明分别测量，order 为序号模 7，取五次公共 API 调用的中位数。无依赖图从 0.96、8.97、122.29 毫秒降至 0.56、1.86、7.24 毫秒。链式依赖图从 0.96、7.67、93.67 毫秒降至 0.68、2.37、9.36 毫秒。默认 linux.dnf 的 52 条声明按每组 100 次、五组中位数测量，单次 API 耗时从 1.011 降至 0.688 毫秒，其中排序从 0.466 降至 0.060 毫秒。

## 2026-11-29 · 共享依赖的安装根解析

Linux x86_64、Python 3.14.10，运行 `./tests/check.sh --strict`，45 项通过。新增公共 API 用例覆盖共享配置依赖、显式选择和声明依赖的顺序、纯配置叶节点、安装项边界及 all 选择。比对五个安装平台的默认与完整 profile，以及三个 Linux 分支的 WSL 默认与完整 profile，共 16 组，安装根列表及顺序均与修改前一致。本轮未在 macOS、Windows 或 WSL 原生运行。

每层两个配置项共享下一层，末端为单个安装项。8、12、16 层合成目录各测五次，`api.installation_roots()` 耗时中位数分别从 0.23、1.47、20.45 毫秒降至 0.20、0.23、0.24 毫秒。

## 2026-11-29 · 声明写出时复用安装 schema

Linux x86_64、Python 3.14.10，运行 `./tests/check.sh --strict`，46 项通过。新增用例验证每批非空声明构造一次 schema，空目录写出无需加载 schema，遇到无效声明时保留先前已写出的文件。对 linux.dnf 原生环境的 54 项选择和全部 67 个内置声明，逐文件比对重构前后的文件名、TOML 字节和公共 API 回读结果，均一致。

两组 schema 文件读取次数分别从 324 和 402 降至 6。写入临时目录各七次，耗时中位数分别从 7.97 降至 2.14 毫秒、从 12.08 降至 4.34 毫秒。本轮未在 macOS、Windows 或 WSL 原生运行。

## 2026-11-29 · 单次快照预览

Linux x86_64、Python 3.14.10，严格检查 46 项通过。129 个文件共 8,388,615 字节的临时快照经过完整校验，单次预览的解压文件数与字节数均为零。CLI、API 与显式会话生成相同计划。未在 macOS、Windows 或 WSL 原生运行。

目录浏览复用链接检查取得的文件状态。128 个普通叶文件的探测从 384 次 stat 和 128 次 lstat 降至 0 次 stat 和 128 次 lstat。严格检查 47 项通过，覆盖缺失、排除、断链、访问错误、空目录及 FIFO。

软件全选复用本次环境筛选结果。linux.dnf 的 native、WSL 两种环境分别从 104、93 次标准化降至 52 次；五个平台共八种适用组合的声明内容与顺序逐项一致。严格检查 48 项通过。

## 2026-11-29 · 声明选择、采集、清单与脚本写入

Linux x86_64、Python 3.14.10。默认声明加载及 linux.dnf native 全选安装根的配置深复制次数从 80、18 均降至零；五个平台八种组合的选择内容、顺序与安装根逐项一致。本轮未在 macOS、Windows 或 WSL 原生运行。
临时目录中 128 个普通叶文件的导出 lstat 次数从 640 降至 384。缺失必需路径时保留 FileNotFoundError 的路径，输出目录没有发布归档。
