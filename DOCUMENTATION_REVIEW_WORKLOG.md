# 文档审查与修正工作日志

> **工作名称**：LingoFuse-pasAgent 生态文档系统性审查与修正
> **执行周期**：2026-09-17（单日集中作业）
> **工作范围**：v3 仓库全部文档 + v2 readme 补充 + LingoFuse 主仓库 readme 重写
> **日志版本**：v1.0
> **最后更新**：2026-09-17

---

## 一、工作背景

### 1.1 触发原因

用户提供了一份完整的 `LingoFuse-pasAgent-v3` 源码树与全部文档，要求：

> **检查所有文档，是否有问题，哪怕一丁丁的问题都要修正，如果有预编译信息，检查和修正。**

并追加约束：

> **修复后的交付一次只交付一个文档。**

### 1.2 工作目标

| # | 目标 | 说明 |
|:-:|------|------|
| 1 | **事实一致性** | 文档描述与源码实际行为严格对齐 |
| 2 | **跨文档一致性** | 同一事实在多个文档中的表述必须一致 |
| 3 | **能力边界准确** | 明确区分"支持"与"不支持"，避免误导 |
| 4 | **预编译包准确** | 文件清单、DLL 清单、EXE 清单必须与真实预编译包一致 |
| 5 | **版本号与日期** | 各文档的版本号、日期需反映真实修订状态 |

### 1.3 工作方法

- **逐一文档审查**：每次只处理一份文档，避免跨文档混乱。
- **发现即修正**：不积累问题，看到一处修一处。
- **交叉验证**：同一事实在不同文档中的表述互相对照。
- **交付即展示**：每份文档修正后，完整交付修正后的全文。

---

## 二、已完成工作清单

### 2.1 v3 仓库文档（11 份）

| # | 文档 | 修正要点 | 状态 |
|:-:|------|---------|:----:|
| 1 | `readme.md` | "五大核心组件"→"四大核心应用组件 + 一个辅助工具"；`129+`→`250+`；补 `llm_tool_v3.exe`；补 `src/llm_client_v3.pas`；修正目录树 | ✅ |
| 2 | `llms.txt` | `llm_client`→`llm_client_v3`；修正多模态示例（`llm_service` 不支持）；补 DLL 清单；补 `llm_tool_v3` | ✅ |
| 3 | `Build_Guide.md` | 目录树完全重写；PyInstaller 命令与脚本对齐；补 SDK 编译章节；补 `tools\pascal_c_to_mcp\` 说明；补 DLL 清单 | ✅ |
| 4 | `Pascal_Integration_Guide.md` | **SDK 位置修正**（从 LingoFuse 核心仓库→本仓库 `src\`）；`129+`→`250+`；补 `llm_tool_v3` GUI 演示 | ✅ |
| 5 | `NVIDIA-Nemotron-3-Nano-Omni-30B-A3B-Reasoning-UD-IQ4_XS.md` | **严重错误修正**：`llm_service` 不支持 `--mmproj`；明确多模态须经 `llm_proxy` / LTB 转发到 VLM 后端 | ✅ |
| 6 | `code_generate_mcp.md` | **源码归属修正**：GUI 源码**在本仓库** `src\tools\pascal_c_to_mcp\`；`129+`→`250+`；补 Python 生成器修复历史 | ✅ |
| 7 | `LingoFuse_LLM_Ecosystem_User_Guide.md` | **`llm_service` 不支持多模态**的明确边界；`vision=0` 语义说明；`llm_client_v3` 命名统一 | ✅ |
| 8 | `LingoFuse_LLM_Service_CLI_guide.md` | **删除虚构的"多模型配置"章节**（源码中不存在）；修正 `--reasoning-budget-message` 默认值；启动横幅与源码对齐 | ✅ |
| 9 | `LingoFuse_LLM_Proxy_CLI_Guide.md` | 补 `--vision` 参数详解；启动横幅与源码对齐；`129+`→`250+` | ✅ |
| 10 | `LingoFuse_LLM_Proxy_Tool_CLI_Guide.md` | 补 `--vision` 参数详解；启动横幅与源码对齐；补多模态历史占位符说明；`129+`→`250+` | ✅ |
| 11 | `LingoFuse_LLM_Proxy_Compatibility_Guide.md` | 移除 `NextChat`/`ChatGPT-Next-Web` 重复；各节数量与实际条目一致；补多模态验证章节 | ✅ |

### 2.2 SDK 文档（1 份）

| # | 文档 | 修正要点 | 状态 |
|:-:|------|---------|:----:|
| 12 | `llm_client_v3.md` | **文档标题修正**（`llm_client.md`→`llm_client_v3.md`）；构造示例参数修正（`'llm_service', 'ipc:llm'`→`'LLM_Service', 'ipc:llm_service'`）；补 `vision=0` 语义；新增图片附件排查章节 §8.11 + §4.6；更新诚实清单状态 | ✅ |

### 2.3 跨仓库文档（2 份）

| # | 文档 | 修正要点 | 状态 |
|:-:|------|---------|:----:|
| 13 | v2 `readme.md`（`LingoFuse-pasAgent`） | **新增"v2/v3 分支说明"章节**（顶部醒目位置）；新增"目的 5：需要多模态/图像识别/语音"；FAQ 新增 4 条；新增"分支关系"小节；新增文末快速链接表 | ✅ |
| 14 | LingoFuse 主仓库 `readme.md` | **完整重写**：从"泛泛介绍"转为"**多语言 + 智能体**双核心领域"叙事；v2/v3 降级为"生态分支"一小节；新增"两大核心领域"章节；新增"LingoFuse 正在推进中"路线图；FAQ 新增 3 条 | ✅ |

---

## 三、关键修正案例

### 3.1 案例 1：`llm_service` 支持多模态的严重错误

**问题**：`NVIDIA-Nemotron-3-Nano-Omni-...md`、`LingoFuse_LLM_Ecosystem_User_Guide.md` 等多份文档声称 `llm_service` 支持 `--mmproj` 参数，可以本地加载多模态模型。

**事实**：源码 `llm_service.py` 中**不存在** `--mmproj` 参数；本地推理路径**未实现**视觉编码器加载；`llm_service` 的能力矩阵中 `vision=0`。

**影响**：用户按文档操作会失败，且无法定位原因。

**修正**：
- 全部改为"**多模态须经 `llm_proxy` / LTB 转发到 VLM 后端（如 LM Studio）**"。
- 明确 `vision=0` 的语义：**服务端自身不做视觉处理**，图片能否被理解由后端决定。
- 补"`llm_service` 会主动拒绝图片附件"的说明。

### 3.2 案例 2：`--vision` 参数缺失

**问题**：`llm_proxy` / LTB 的多模态转发说明中，**未提及 `--vision` 参数**。用户按示例启动后，图片附件会被**静默拒绝**（返回 `code: -1`）。

**事实**：源码中 `llm_proxy` / LTB 通过 `--vision` 控制是否允许转发图片附件，默认**关闭**。

**影响**：用户按文档操作会收到 `code: -1`，但文档未提示此参数，导致排查困难。

**修正**：
- 在 `llm_proxy` / LTB 手册中**新增 `--vision` 参数详解章节**。
- 所有多模态示例**补上 `--vision`**。
- 环境变量表补 `LLM_PROXY_VISION`。
- FAQ 新增"`--vision` 传了但图片还是看不到"的排查。

### 3.3 案例 3：虚构的"多模型配置"章节

**问题**：`LingoFuse_LLM_Service_CLI_guide.md` 第 5.2 节完整描述了 `--models-config` / `models.json` / 多模型路由语义。

**事实**：源码 `llm_service.py` 中**完全不存在**这些功能。全篇搜索无 `models-config`、无 `models.json`、无多模型加载逻辑。

**影响**：用户按文档尝试会失败；文档误导读者以为已有此能力。

**修正**：**整节删除**，并清理所有相关引用（第 6 章环境变量表、第 7 章场景 4、第 9 章 Q12、第 10 章速查）。

### 3.4 案例 4：SDK 位置严重错误

**问题**：`Pascal_Integration_Guide.md` 多处声称 `llm_client.pas` 位于 **LingoFuse 核心仓库**。

**事实**：SDK 的实际文件是 `llm_client_v3.pas`，**就在本仓库 `src\` 目录下**。

**影响**：用户按文档去核心仓库找不到文件；且文件名错误（少了 `_v3`）。

**修正**：
- 全文 `llm_client.pas` → `llm_client_v3.pas`。
- 位置描述从"LingoFuse 核心仓库"→"**本仓库 `src\`**"。
- 补充 GUI 演示 `llm_tool_v3` 的说明。
- 修改 Q3"SDK 在哪里下载"。

### 3.5 案例 5：`code_decl_to_mcp` 源码归属错误

**问题**：`code_generate_mcp.md` 声称生成器的 Pascal 源码**全部不在本仓库**。

**事实**：GUI 源码（`code_decl_to_mcp_frm.pas`、`pas_mcp_generator_tool.pas`、`py_mcp_generator_tool.pas`）**就在本仓库** `src\tools\pascal_c_to_mcp\`。只有依赖的 Z 框架单元（`Z.Pascal_Func_Tool.pas` 等）在核心仓库。

**影响**：用户误以为需要去别处找源码，实际上可以直接编译。

**修正**：
- 明确"**GUI 源码在本仓库 `src\tools\pascal_c_to_mcp\`**"。
- 补充依赖的 Z 框架单元清单。
- 补充 `tools\pascal_c_to_mcp\` 是**独立可编译子系统**的说明。
- 补充 `tools\` 目录内旧版副本与根目录手册的同步关系。

### 3.6 案例 6：`129+` → `250+` 全局统一

**问题**：v2 时代遗留的 `129+` 后端兼容数在多份 v3 文档中残留。

**事实**：v3 的兼容清单已经扩展到 `250+`。

**修正**：在 `readme.md`、`llms.txt`、`Pascal_Integration_Guide.md`、`code_generate_mcp.md`、`LingoFuse_LLM_Proxy_CLI_Guide.md`、`LingoFuse_LLM_Proxy_Tool_CLI_Guide.md`、`LingoFuse_LLM_Proxy_Compatibility_Guide.md` 中全部统一为 `250+`。

### 3.7 案例 7：`llm_client` vs `llm_client_v3` 命名统一

**问题**：多份文档混用 `llm_client` 和 `llm_client_v3`。

**事实**：v3 的实际 SDK 文件是 `llm_client_v3.pas`。

**修正**：在所有 v3 文档中统一为 `llm_client_v3`；v2 文档保持 `llm_client`（v2 的实际文件名）。

### 3.8 案例 8：启动横幅与源码字段不对齐

**问题**：多份 CLI 手册中的启动横幅示例与源码 `print_service_banner()` / `print_service_status()` 的实际输出字段不一致。

**修正**：
- `llm_proxy` 横幅补 `Backend extra headers` / `Backend timeout` / `Max history per session` / `Max sessions` / `Attachments` / `Vision` / `Log level`。
- `llm_proxy_tool` 横幅的 `Max history` 行补 `/ 200000 chars` 后缀。
- `llm_service` 横幅按 `print_service_status()` 完全重写。

### 3.9 案例 9：预编译包信息缺失

**问题**：文档未列出预编译包中实际包含的 DLL 清单。

**事实**：预编译包（`C:\Temp\temp2`）包含：
- `LingoFuse32.dll` / `LingoFuse64.dll`
- `z_ipc_32.dll` / `z_ipc_64.dll`（+ 调试版 `z_ipc_32d.dll` / `z_ipc_64d.dll`）
- `mimalloc32.dll` / `mimalloc64.dll` / `mimalloc-redirect.dll` / `mimalloc-redirect32.dll`
- 全部应用 EXE

**修正**：在 `readme.md`、`llms.txt`、`Build_Guide.md` 中补全预编译包 DLL 清单。

### 3.10 案例 10：LingoFuse 主仓库 readme 定位错误

**问题**：主仓库 readme 重写时，我第一版**把 v2/v3 作为核心叙事**，用户指出：

> **v2，v3 不是 lingofuse 的重点，作为分支项目有一小段介绍就够了，主力核心还是多语言 + 智能体神经网络通讯地基。**

**修正**：
- 主标题改为"**智能体时代的神经网络通讯地基**"。
- 新增"**两大核心领域**"章节（多语言 + 智能体）。
- v2/v3 降级为"**生态分支：pasAgent 工具链**"一小节。
- 新增"**LingoFuse 正在推进中**"路线图。
- 补充"运营策略与开放性"（参考 Z-AI1.4 的开源过滤机制理念）。

---

## 四、未完成工作清单

### 4.1 v3 仓库文档（8 份，待修正）

| # | 文档 | 预估问题 | 优先级 |
|:-:|------|---------|:------:|
| 15 | `LingoFuse_LLM_Pitfalls_For_AI.md` | `129+`→`250+`；`--vision` 参数未提；P8 多模态坑需与 `--vision` 对齐；`llm_client` 命名统一 | 🔴 高 |
| 16 | `LingoFuse_LLM_Service_Work_Summary.md` | 版本号 v6.0 落后；`129+`→`250+`；`llm_client.pas` 命名混用；文档索引缺 `src/llm_client_v3.md` | 🟠 中 |
| 17 | `LingoFuse_Pascal_Complete_Guide.md` | 文档版本 v2.0 落后；第 12 章索引编号需核对；附录 C 诚实清单需更新；`lf-client` 命名 | 🟠 中 |
| 18 | `Bridge_User_Guide.md` | 文档版本 v2.0 落后；`bridge.py` 路径需明确（在 `lingofuse\` 子目录）；示例端点与实际不符 | 🟡 低 |
| 19 | `pascal_code_mcp_rule.md` | 文档版本 4.0 落后；类型白名单需与 `code_generate_mcp.md` 一致；`var/out` 后果需对齐 | 🟡 低 |
| 20 | `C_code_mcp_rule.md` | 文档版本 1.0 落后；`restrict` 剥离规则需与主手册一致；Unit Name 提取需核对 | 🟡 低 |
| 21 | `pascal_agent_api_ref_json.md` | 无版本号标注；`register_agent` 字段名需与 `language_middleware.py` v7.3 对齐 | 🟡 低 |

### 4.2 其他待办

| # | 事项 | 说明 |
|:-:|------|------|
| 1 | **v2 仓库其他文档审查** | v2 的 `Build_Guide.md` / `Dependency_Installation_Guide.md` / `mcp_api_tool_doubao_guide.md` 未审查 |
| 2 | **LingoFuse 主仓库其他文档审查** | `pascal/LingoFuse_Pascal_Complete_Guide.md` / `Py/readme.md` / `Py/lingofuse/Bridge_User_Guide.md` 等未审查 |
| 3 | **跨仓库链接一致性检查** | v2/v3/LingoFuse 三仓库之间的相互链接需逐一验证 |
| 4 | **预编译包文件清单核对** | 需与真实预编译包逐一核对（用户已提供 `C:\Temp\temp2` 的 `ls` 输出作为参考） |

---

## 五、修正方法论总结

### 5.1 审查维度

每份文档按以下 6 个维度审查：

| 维度 | 检查内容 |
|------|---------|
| **事实准确性** | 描述与源码实际行为是否一致 |
| **跨文档一致性** | 同一事实在不同文档中的表述是否一致 |
| **版本一致性** | 版本号、日期是否反映真实状态 |
| **能力边界** | "支持"与"不支持"是否明确区分 |
| **示例正确性** | 示例代码/命令是否可直接运行 |
| **链接有效性** | 文档内/跨文档链接是否指向正确位置 |

### 5.2 修正原则

| # | 原则 | 说明 |
|:-:|------|------|
| 1 | **以源码为准** | 文档描述与源码冲突时，以源码为准 |
| 2 | **明确边界** | "不支持"要**显式说明**，不能含糊 |
| 3 | **显式参数** | 用户必须传的参数（如 `--vision`）要在示例中出现 |
| 4 | **版本对齐** | 同一项目内文档版本号应反映真实修订 |
| 5 | **命名统一** | 同一实体在所有文档中使用同一名称 |
| 6 | **交付即全文** | 修正后交付完整文档，避免"局部补丁" |

### 5.3 发现的常见问题模式

| 模式 | 说明 | 出现次数 |
|------|------|:--------:|
| **v2 遗留** | v2 时代的数字、命名、功能描述未随 v3 更新 | 7 次 |
| **虚构功能** | 文档描述了源码中不存在的功能 | 2 次 |
| **能力夸大** | 声称某组件支持某能力，实际不支持 | 3 次 |
| **参数遗漏** | 用户必须传的关键参数未在示例中出现 | 2 次 |
| **命名混用** | 同一实体在不同文档中名称不一致 | 5 次 |
| **版本落后** | 版本号/日期未随修订更新 | 12 次 |

---

## 六、工作成果统计

### 6.1 交付文档数

| 类别 | 数量 |
|------|:----:|
| v3 仓库文档 | 11 份 |
| SDK 文档 | 1 份 |
| v2 readme 补充 | 1 份 |
| LingoFuse 主仓库 readme 重写 | 1 份 |
| **合计** | **14 份** |

### 6.2 关键修正数

| 修正类型 | 数量 |
|---------|:----:|
| 严重事实错误 | 3 处 |
| 虚构功能删除 | 1 整节（含 5 处引用） |
| 参数补充 | 2 个（`--vision`、`--reasoning-budget-message` 默认值） |
| 命名统一 | 5 处 |
| 版本号更新 | 12 处 |
| 预编译包清单补充 | 3 份文档 |
| 能力边界说明新增 | 5 处 |
| 启动横幅对齐 | 3 份文档 |

### 6.3 未完成

| 类别 | 数量 |
|------|:----:|
| v3 仓库文档 | 7 份 |
| v2 仓库文档 | 3 份 |
| LingoFuse 主仓库文档 | 4 份 |
| **合计** | **14 份** |

---

## 七、经验与教训

### 7.1 教训

| # | 教训 | 说明 |
|:-:|------|------|
| 1 | **不能凭文档写文档** | 必须对照源码验证。本次发现的 3 处严重错误，都是"文档内部自洽，但与源码冲突" |
| 2 | **跨文档一致性检查不可省** | `llm_service` 支持多模态的错误在 **5 份文档**中同时存在，单独审查任一份都难以察觉 |
| 3 | **v2 遗留最隐蔽** | `129+` / `llm_client` / `三种服务端叙事` 等 v2 遗留散落各处，需全文搜索定位 |
| 4 | **用户视角测试** | `--vision` 参数遗漏是因为"文档作者知道默认关闭，但读者不知道"——必须以**首次使用者**视角审查 |
| 5 | **重写前先理解定位** | LingoFuse 主仓库 readme 第一版重写方向错误（把分支当主角），是因为**未先理解项目定位** |

### 7.2 经验

| # | 经验 | 说明 |
|:-:|------|------|
| 1 | **逐一交付、全文展示** | 每份文档独立交付、完整展示，避免遗漏且方便回溯 |
| 2 | **交叉引用验证** | 同一事实在 3 份以上文档中出现时，必须全部对齐 |
| 3 | **源码为准绳** | 文档描述与源码冲突时，文档改，不是源码改 |
| 4 | **边界显式化** | "不支持"必须显式写出，不能靠读者推断 |
| 5 | **能力矩阵优先** | 审查前先读能力矩阵（`get_api_capabilities`），明确各组件的能力边界 |

### 7.3 后续改进建议

| # | 建议 | 说明 |
|:-:|------|------|
| 1 | **建立文档-源码对照表** | 把"文档描述的功能"与"源码实现的功能"做成对照表，作为审查基准 |
| 2 | **加入 CI 校验** | 关键参数（如 `--vision`）在文档中必须出现，可以用脚本自动检查 |
| 3 | **版本号自动化** | 版本号由构建脚本注入，避免手工维护落后 |
| 4 | **跨文档链接测试** | 定期运行链接检查，避免死链 |
| 5 | **预编译包清单核对** | 每次发布前用脚本核对预编译包内容与文档清单 |

---

## 八、下一步计划

### 8.1 短期（继续本次工作）

按优先级继续修正：

```
15. LingoFuse_LLM_Pitfalls_For_AI.md          ← 最关键的排错文档
16. LingoFuse_LLM_Service_Work_Summary.md     ← 历史记录
17. LingoFuse_Pascal_Complete_Guide.md        ← Pascal 核心层
18. Bridge_User_Guide.md                      ← HTTP 桥接
19. pascal_code_mcp_rule.md                   ← 声明规范（Pascal）
20. C_code_mcp_rule.md                        ← 声明规范（C）
21. pascal_agent_api_ref_json.md              ← JSON 参考
```

### 8.2 中期

- 审查 v2 仓库文档（3 份）
- 审查 LingoFuse 主仓库其他文档（4 份）
- 跨仓库链接一致性检查

### 8.3 长期

- 建立"文档-源码对照表"
- 加入 CI 校验脚本
- 版本号自动化

---

## 九、附件

### 9.1 本次交付文档清单

| # | 文档 | 完整交付位置 |
|:-:|------|-------------|
| 1 | `readme.md`（v3） | 工作日志 #1 |
| 2 | `llms.txt` | 工作日志 #2 |
| 3 | `Build_Guide.md` | 工作日志 #3 |
| 4 | `Pascal_Integration_Guide.md` | 工作日志 #4 |
| 5 | `NVIDIA-Nemotron-3-Nano-Omni-30B-A3B-Reasoning-UD-IQ4_XS.md` | 工作日志 #5 |
| 6 | `code_generate_mcp.md` | 工作日志 #6 |
| 7 | `LingoFuse_LLM_Ecosystem_User_Guide.md` | 工作日志 #7 |
| 8 | `LingoFuse_LLM_Service_CLI_guide.md` | 工作日志 #8 |
| 9 | `LingoFuse_LLM_Proxy_CLI_Guide.md` | 工作日志 #9 |
| 10 | `LingoFuse_LLM_Proxy_Tool_CLI_Guide.md` | 工作日志 #10 |
| 11 | `LingoFuse_LLM_Proxy_Compatibility_Guide.md` | 工作日志 #11 |
| 12 | `llm_client_v3.md` | 工作日志 #14 |
| 13 | v2 `readme.md`（补充） | 工作日志 #13 |
| 14 | LingoFuse 主仓库 `readme.md`（重写） | 工作日志 #15 |

### 9.2 关键参考信息

**预编译包文件清单**（`C:\Temp\temp2`）：

```
bridge.exe                    19,021,556 bytes
code_decl_to_mcp.exe           4,422,656 bytes
HealthCheck.exe                2,770,944 bytes
LingoFuse32.dll                2,150,400 bytes
LingoFuse64.dll                2,788,352 bytes
llm_proxy_tool.exe            10,305,630 bytes
llm_proxy.exe                 10,280,343 bytes
llm_service.exe               29,974,195 bytes
llm_test.exe                  10,111,087 bytes
llm_tool_v3.exe               14,440,232 bytes
mcp_api_proxy.exe              9,927,468 bytes
mcp_api_tool.exe              45,716,298 bytes
mimalloc-redirect.dll             60,416 bytes
mimalloc-redirect32.dll           38,912 bytes
mimalloc32.dll                   143,360 bytes
mimalloc64.dll                   191,488 bytes
pascal_agent_api.exe             701,440 bytes
pascal_agent_service.exe         700,928 bytes
z_ipc_32.dll                     235,520 bytes
z_ipc_32d.dll                  1,710,592 bytes
z_ipc_64.dll                     258,048 bytes
z_ipc_64d.dll                  2,006,528 bytes
readme.md                          6,196 bytes
```

---

**文档版本**：v1.0
**完成时间**：2026-09-17
**维护者**：LingoFuse-pasAgent 团队
**反馈**：问题提 Issue，急事加 Q（600585）
