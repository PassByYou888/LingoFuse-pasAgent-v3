# Structured Output 完整学习指南

> **文档定位**：从概念起源到工程实践，系统讲解大模型"结构化输出"这一能力。
>
> **适用读者**：
> - 第一次接触 Structured Output 的开发者
> - 需要让模型返回严格 JSON 的后端/客户端工程师
> - 想理解"为什么我的提示词不生效"的调试者
> - 需要在 LingoFuse 生态中做目标检测、信息抽取的人
>
> **文档版本**：v1.1（目录对齐版）
> **最后更新**：2026-09-24
> **相关文档**：
> - [`llm_client_v3.md`](llm_client_v3.md) §16 —— Pascal 客户端结构化输出实践
> - [`LingoFuse_LLM_Proxy_CLI_Guide.md`](LingoFuse_LLM_Proxy_CLI_Guide.md) §5.5 —— 代理层多模态转发
> - [`LingoFuse_LLM_Proxy_Tool_CLI_Guide.md`](LingoFuse_LLM_Proxy_Tool_CLI_Guide.md) §4.5 —— LTB 多模态转发
> - [`NVIDIA-Nemotron-3-Nano-Omni-30B-A3B-Reasoning-UD-IQ4_XS.md`](NVIDIA-Nemotron-3-Nano-Omni-30B-A3B-Reasoning-UD-IQ4_XS.md) —— 推荐 VLM 模型

---

## 目录

- [第一章 起源：LLM 输出格式的困境](#第一章-起源llm-输出格式的困境)
- [第二章 什么是 Structured Output](#第二章-什么是-structured-output)
- [第三章 规范：OpenAI Structured Outputs](#第三章-规范openai-structured-outputs)
- [第四章 JSON Schema 基础](#第四章-json-schema-基础)
- [第五章 后端实现：LM Studio / Ollama / vLLM](#第五章-后端实现lm-studio--ollama--vllm)
- [第六章 典型应用场景](#第六章-典型应用场景)
- [第七章 在 LingoFuse 生态中的实践](#第七章-在-lingofuse-生态中的实践)
- [第八章 最佳实践](#第八章-最佳实践)
- [第九章 与其他方案的对比](#第九章-与其他方案的对比)
- [第十章 未来展望](#第十章-未来展望)
- [附录](#附录)

---

## 第一章 起源：LLM 输出格式的困境

### 1.1 自由文本：最初的美好与后来的痛苦

大语言模型（LLM）诞生时，输出的就是**自由文本**。这既是它的魅力，也是它的诅咒。

**魅力**：

```
用户：介绍一下巴黎。
模型：巴黎是法国的首都，位于塞纳河畔。它以浪漫著称，
      拥有埃菲尔铁塔、卢浮宫、圣母院等世界著名景点。
      人口约 214 万（市区）。……
```

**诅咒**：

```
用户：提取这段话里的所有城市名，用 JSON 格式。
模型：好的，这是城市列表：
      ["巴黎", "伦敦", "纽约"]
      希望能帮到你！
```

问题来了：**模型输出了合法 JSON，但前面加了废话，后面还加了"希望能帮到你"**。

你要的只是 `["巴黎", "伦敦", "纽约"]`，但你拿到的是：

```
好的，这是城市列表：
["巴黎", "伦敦", "纽约"]
希望能帮到你！
```

### 1.2 提示词工程的应急方案

第一代解决方案是**提示词工程（Prompt Engineering）**：在提示词里反复强调"只输出 JSON，不要任何其他文字"。

```text
You are a helpful assistant that ONLY outputs valid JSON.
DO NOT include any explanation, markdown fences, or extra text.
Output must start with { and end with }.

User: Extract all city names from: "Paris, London, New York are great"
```

效果如何？

| 场景 | 成功率（实测） |
|------|:-------------:|
| GPT-4 简单任务 | ~85% |
| GPT-3.5 简单任务 | ~65% |
| 7B 级开源模型 | ~40% |
| 复杂嵌套结构 | <20% |

**关键问题**：
- **不确定性**：同一个提示词，不同调用可能给出不同格式。
- **模型会"帮忙"**：小模型很容易"多嘴"，加上解释性文字。
- **难以调试**：失败时没有明确的原因，只能靠猜。
- **不是 100% 可靠**：在生产环境，85% 成功率意味着每天有 15% 的请求失败。

### 1.3 事后解析：脆弱的补丁

第二代解决方案是**事后解析**：让模型自由输出，然后用正则表达式或启发式规则提取 JSON。

```python
import json
import re

def extract_json(text):
    # 尝试直接解析
    try:
        return json.loads(text)
    except:
        pass

    # 尝试从代码块中提取
    match = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1))
        except:
            pass

    # 尝试从第一个 { 到最后一个 }
    start = text.find('{')
    end = text.rfind('}')
    if start >= 0 and end > start:
        try:
            return json.loads(text[start:end+1])
        except:
            pass

    # 尝试修复常见的 JSON 错误
    # ...

    raise ValueError("Could not extract JSON")
```

**这段代码的每一个 `try/except` 都是血和泪**。它的问题是：

1. **永远在打补丁**：模型会以你想象不到的方式出错。
2. **失败时无法恢复**：一旦解析失败，只能重新调用。
3. **性能浪费**：明明只需要 3 个 token，模型生成了 30 个。
4. **语义漂移**：`{"name": "Paris"}` 和 `{"city_name": "Paris"}` 可能都符合你的解析器，但字段名不一致。

### 1.4 一个真实场景：图像检测器

让我们看一个非常具体的场景，说明为什么需要 Structured Output。

**任务**：给模型一张图片，让它检测图片中的所有物体，返回每个物体的**类别**和**边界框**。

**期望的输出**：

```json
{
  "detections": [
    {"label": "person", "bbox": [0.12, 0.23, 0.45, 0.78], "confidence": 0.95},
    {"label": "dog",    "bbox": [0.50, 0.30, 0.80, 0.65], "confidence": 0.88}
  ]
}
```

**用纯提示词方式，你可能得到的输出**：

**情况 1：加了说明**（JSON 前后有文字）

```
根据图像分析，我检测到以下目标：
{"detections": [...]}
希望对你有帮助。
```

**情况 2：字段名漂移**（模型认为更合理的字段名）

```json
{
  "objects": [
    {"class": "person", "box": [0.12, 0.23, 0.45, 0.78], "score": 0.95}
  ]
}
```

**情况 3：坐标格式不符**（模型用了像素坐标）

```json
{
  "detections": [
    {"label": "person", "bbox": [123, 234, 456, 789]}
  ]
}
```

**情况 4：方框顺序错误**（模型用了 `[y1, x1, y2, x2]`）

```json
{
  "detections": [
    {"label": "person", "bbox": [0.23, 0.12, 0.78, 0.45]}
  ]
}
```

**情况 5：置信度用百分数**

```json
{
  "detections": [
    {"label": "person", "bbox": [...], "confidence": 95}
  ]
}
```

**情况 6：模型拒绝输出 JSON，用自然语言描述**

```
图中左侧有一个站立的人，大约占据画面左半部分；
右侧有一只小狗，位于画面中下部。
```

**六种情况，只有一种（或零种）符合预期**。这就是纯提示词方案的现实。

**Structured Output 的解决方案**：不是"求"模型输出 JSON，而是**在模型生成 token 的层面强制约束**。

---

## 第二章 什么是 Structured Output

### 2.1 定义

**Structured Output（结构化输出）** 是一种让大语言模型**严格按照调用方提供的 JSON Schema 生成输出**的能力。

**关键点**：
- **不是"请求"**：不是让模型"尽量"输出符合格式的内容。
- **而是"约束"**：是在模型生成 token 时**在解码层面强制**。
- **由 schema 驱动**：输出的结构由调用方提供的 JSON Schema 完全决定。

### 2.2 与传统方案的对比

| 维度 | 纯提示词 | 事后解析 | **Structured Output** |
|------|:--------:|:--------:|:---------------------:|
| **成功率** | 40-85% | 60-90% | **100%**（合法 JSON） |
| **字段名一致** | ❌ | ❌ | **✅** |
| **类型一致** | ❌ | ⚠️ | **✅** |
| **必填字段** | ❌ | ❌ | **✅** |
| **多余字段** | ⚠️ | ❌ | **✅** 严格模式下禁止 |
| **性能开销** | 低 | 中（解析成本） | **低**（几乎零额外开销） |
| **实现复杂度** | 低 | 高（各种补丁） | **低**（调用方只需传 schema） |
| **可调试性** | 差 | 中 | **好**（失败原因明确） |

**一句话总结**：

> **Structured Output = 用 JSON Schema 换掉"提示词 + 事后解析"的组合，得到 100% 可靠的 JSON。**

### 2.3 核心原理：约束解码

Structured Output 的核心是**约束解码（Constrained Decoding）**。

**普通解码流程**：

```
当前上下文 → 模型 → 概率分布（词汇表上所有 token 的概率）→ 采样 → 下一个 token
```

例如，模型要生成下一个 token 时，概率分布可能是：

```
"{" : 0.35
"Hello" : 0.15
"好的" : 0.12
"[" : 0.08
...（几万个 token）
```

**约束解码流程**：

```
当前上下文 + 当前 schema 状态 → 模型 → 概率分布
                                            ↓
                                    应用 schema 掩码
                                            ↓
                                    只保留合法 token
                                            ↓
                                        重新归一化
                                            ↓
                                          采样
```

例如，如果 schema 要求下一个 token 必须是 `{`（因为必须从对象开始），那么：

```
"{" : 0.35   → 保留
"Hello" : 0.15   → 掩码为 0
"好的" : 0.12   → 掩码为 0
"[" : 0.08   → 掩码为 0
...（其他所有 token 掩码为 0）

重新归一化后：
"{" : 1.0   → 必须选它
```

**实现方式**（后端角度）：

1. **JSON Schema → 有限状态机（FSM）**：
   把 schema 编译成一个状态机，每个状态代表"当前已生成的部分 JSON 在 schema 中的位置"。

2. **每个状态对应一个合法 token 集合**：
   状态机告诉你"在当前位置，下一个 token 可以是哪些"。

3. **token 掩码**：
   在每一步采样前，把非法 token 的概率设为 0（或 -∞）。

4. **token 到状态的转移**：
   生成一个 token 后，根据 token 更新状态机。

**关键洞察**：
- **不影响模型能力**：模型仍然是原来那个模型，只是"选择范围"被限制了。
- **无额外推理成本**：掩码操作几乎零开销（相比模型推理本身）。
- **100% 保证**：只要 token 序列生成完毕，就一定是合法 JSON。

### 2.4 关键术语

| 术语 | 含义 |
|------|------|
| **JSON Schema** | 描述 JSON 数据结构的规范（IETF 标准） |
| **Constrained Decoding** | 约束解码，在解码时限制 token 选择 |
| **FSM** | 有限状态机，schema 编译后的形式 |
| **Token Mask** | token 掩码，把非法 token 的概率设为零 |
| **Structured Outputs** | OpenAI 对"约束解码 + JSON Schema"的官方名称 |
| **JSON Mode** | 更简单的模式，只保证"合法 JSON"，不保证结构 |
| **Function Calling** | 相关但不同的机制，用于调用外部函数 |
| **response_format** | OpenAI API 中控制输出格式的字段 |

### 2.5 三个层次：从最松到最严

理解 Structured Output 时，要注意三个不同的层次：

**层次 1：无约束**

```
模型：自由输出任何文本
风险：完全不可控
```

**层次 2：JSON Mode（`{"type": "json_object"}`）**

```
模型：保证输出合法 JSON
风险：
  - 不保证字段名
  - 不保证字段类型
  - 不保证必填字段
  - 可能输出 `{"answer": "..."}` 而你期望 `{"result": "..."}`
```

**层次 3：Structured Outputs（`{"type": "json_schema", ...}`）**

```
模型：保证输出符合指定 JSON Schema
保证：
  ✅ 合法 JSON
  ✅ 字段名一致
  ✅ 字段类型一致
  ✅ 必填字段都存在
  ✅ 严格模式下禁止未定义字段
```

**建议**：生产环境**永远用层次 3**（Structured Outputs）。层次 2 只在极少数"我只要合法 JSON，什么结构都行"的场景有用。

---

## 第三章 规范：OpenAI Structured Outputs

### 3.1 起源与演进

时间线：

| 时间 | 事件 |
|------|------|
| 2023 年 6 月 | OpenAI 推出 **Function Calling**（相关但不同） |
| 2023 年 11 月 | OpenAI 推出 **JSON Mode**（`response_format: {"type": "json_object"}`） |
| 2024 年 8 月 | OpenAI 推出 **Structured Outputs**（`response_format: {"type": "json_schema", ...}`） |
| 2024 年 9 月 | LM Studio 0.3.0 支持 Structured Outputs |
| 2024 年 10 月 | Ollama 0.3.0 支持 Structured Outputs |
| 2024 年底 | vLLM、SGLang 等推理引擎陆续支持 |
| 2025 年 | 成为业界事实标准 |

**OpenAI 的规范被业界广泛采纳**，LM Studio、Ollama、vLLM、DeepSeek 等都使用兼容的 API 格式。

### 3.2 `response_format` 字段

`response_format` 是 OpenAI Chat Completions API 的一个参数，用于控制模型输出格式。

**位置**：在请求体的顶层，与 `messages`、`model` 同级。

```json
{
  "model": "gpt-4o-2024-08-06",
  "messages": [...],
  "response_format": { ... }   ← 这里
}
```

**两种类型**：

**类型 A：JSON Mode**

```json
{
  "response_format": {
    "type": "json_object"
  }
}
```

- 只保证输出合法 JSON。
- 不约束结构。
- **注意**：OpenAI 要求提示词中必须提到"JSON"关键字，否则报错。

**类型 B：Structured Outputs**

```json
{
  "response_format": {
    "type": "json_schema",
    "json_schema": {
      "name": "my_response",
      "strict": true,
      "schema": { ... JSON Schema ... }
    }
  }
}
```

- 保证输出严格符合 schema。
- 支持 `strict: true` 禁止未定义字段。

### 3.3 `json_schema` 子对象

`json_schema` 的结构：

```json
{
  "name": "object_detection",     // schema 名字（英文标识符）
  "strict": true,                  // 是否严格模式（布尔值）
  "schema": { ... }                // JSON Schema 本体
}
```

**`name` 字段**：
- 用途：给 schema 起一个名字。
- 要求：符合正则 `^[a-zA-Z0-9_-]+$`，最长 64 字符。
- 作用：OpenAI 用它做缓存优化；对模型无直接影响。
- 建议：用描述性名字，如 `object_detection`、`invoice_extraction`。

**`strict` 字段**：
- `true`：严格模式。模型**必须**输出恰好符合 schema 的对象，不能有任何额外字段。
- `false`：宽松模式。模型可以输出 schema 之外的其他字段。
- **建议**：生产环境永远用 `true`。

**`schema` 字段**：
- 就是标准的 JSON Schema 对象。
- 详见第四章。

### 3.4 支持的 JSON Schema 子集

⚠️ **重要**：OpenAI Structured Outputs **不支持完整的 JSON Schema 规范**，只支持一个**子集**。

**支持的关键字**：

| 类型 | 支持的关键字 |
|------|------------|
| **`type`** | `string`、`number`、`integer`、`boolean`、`object`、`array`、`null` |
| **`properties`** | ✅ |
| **`required`** | ✅ |
| **`items`** | ✅ |
| **`enum`** | ✅ |
| **`anyOf`** | ✅（用于表示可空） |
| **`$defs` / `$ref`** | ✅（递归定义） |
| **`description`** | ✅（强烈推荐） |
| **`additionalProperties: false`** | ✅（严格模式必须） |

**不支持的关键字**：

| 关键字 | 原因 |
|------|------|
| `oneOf` | 需要更复杂的状态机 |
| `allOf` | 同上 |
| `not` | 同上 |
| `if/then/else` | 同上 |
| `patternProperties` | 同上 |
| `minProperties` / `maxProperties` | 同上 |
| `pattern`（正则） | 部分后端支持有限 |
| `minimum` / `maximum` | ⚠️ 支持不完整（不同后端差异大） |
| `minLength` / `maxLength` | ⚠️ 同上 |
| `format` | ⚠️ 部分支持 |

**实践建议**：
- **只用上表中"支持"的关键字**。
- **`minimum` / `maximum` 在部分后端可能被忽略**——不要依赖它们做严格约束，用 `description` 补充说明。
- **避免深层嵌套**——某些后端对深度有限制（通常 5 层）。

### 3.5 常见错误

**错误 1：多嵌套一层 `json_schema`**

```json
{
  "type": "json_schema",
  "json_schema": {
    "json_schema": {    ← ❌ 多了一层
      "name": "...",
      "schema": { ... }
    }
  }
}
```

**正确**：

```json
{
  "type": "json_schema",
  "json_schema": {
    "name": "...",
    "strict": true,
    "schema": { ... }
  }
}
```

**错误 2：`strict` 用了字符串**

```json
{
  "json_schema": {
    "strict": "true"    ← ❌ 字符串
  }
}
```

**正确**：

```json
{
  "json_schema": {
    "strict": true    ← ✅ 布尔值
  }
}
```

**错误 3：`required` 缺失**

```json
{
  "type": "object",
  "properties": {
    "name": { "type": "string" }
  }
  // ❌ 没有 required，模型可能不输出 name
}
```

**正确**：

```json
{
  "type": "object",
  "properties": {
    "name": { "type": "string" }
  },
  "required": ["name"]
}
```

**错误 4：用了 `oneOf`**

```json
{
  "oneOf": [    ← ❌ 不支持
    { "type": "string" },
    { "type": "number" }
  ]
}
```

**替代方案**：用 `anyOf`（如果后端支持）或简化 schema。

**错误 5：`additionalProperties` 未设**

严格模式下，**所有对象必须**显式设置：

```json
{
  "type": "object",
  "properties": { ... },
  "required": [ ... ],
  "additionalProperties": false    ← 必须
}
```

---

## 第四章 JSON Schema 基础

### 4.1 为什么是 JSON Schema

**JSON Schema** 是一种**描述和验证 JSON 数据结构**的标准。它的优势：

1. **机器可读**：可以被程序解析和处理。
2. **表达能力强**：可以描述几乎任何数据结构。
3. **成熟标准**：IETF 标准，有丰富的工具生态。
4. **可组合**：支持引用、递归、条件等高级特性。
5. **人类友好**：JSON 格式本身，容易阅读和编辑。

**Structured Output 选择 JSON Schema 的原因**：
- 它已经是一个**广泛使用的标准**。
- 可以**自动转换为状态机**（用于约束解码）。
- 生态成熟（各类语言的库、验证工具）。

### 4.2 基础结构

一个最简单的 JSON Schema：

```json
{
  "type": "object",
  "properties": {
    "name": { "type": "string" },
    "age": { "type": "integer" }
  },
  "required": ["name", "age"]
}
```

**解释**：
- `type: "object"`：描述一个 JSON 对象。
- `properties`：列出所有可能的字段。
- `required`：哪些字段是必需的。

**对应的合法 JSON**：

```json
{ "name": "Alice", "age": 30 }
```

**对应的非法 JSON**：

```json
{ "name": "Alice" }                    // ❌ 缺 age
{ "name": 123, "age": 30 }             // ❌ name 不是字符串
{ "name": "Alice", "age": 30, "x": 1 } // ❌ 有额外字段（严格模式下）
```

### 4.3 类型系统

**基本类型**：

| JSON Schema 类型 | 对应 JSON | 示例 |
|-----------------|-----------|------|
| `string` | 字符串 | `"hello"` |
| `number` | 数字（含小数） | `3.14` |
| `integer` | 整数 | `42` |
| `boolean` | 布尔值 | `true` / `false` |
| `null` | 空值 | `null` |
| `object` | 对象 | `{...}` |
| `array` | 数组 | `[...]` |

**`number` vs `integer`**：
- `integer`：只允许整数（`42`）。
- `number`：允许整数和小数（`42`、`3.14`）。
- **建议**：如果是整数场景，用 `integer`。

### 4.4 对象与数组

**对象**：

```json
{
  "type": "object",
  "properties": {
    "field1": { "type": "string" },
    "field2": { "type": "integer" }
  },
  "required": ["field1"],
  "additionalProperties": false
}
```

**关键**：
- `properties`：所有可能字段。
- `required`：必填字段（其余为可选）。
- `additionalProperties: false`：严格模式必须（禁止未定义字段）。

**数组**：

```json
{
  "type": "array",
  "items": { "type": "string" },
  "minItems": 0,
  "maxItems": 10
}
```

**关键**：
- `items`：数组元素的 schema。
- `minItems` / `maxItems`：元素数量限制（部分后端支持）。

**对象数组**（最常见的结构）：

```json
{
  "type": "object",
  "properties": {
    "detections": {
      "type": "array",
      "items": {
        "type": "object",
        "properties": {
          "label": { "type": "string" },
          "confidence": { "type": "number" }
        },
        "required": ["label", "confidence"],
        "additionalProperties": false
      }
    }
  },
  "required": ["detections"],
  "additionalProperties": false
}
```

### 4.5 描述字段 `description`

**强烈建议**给每个字段加 `description`。它不影响结构约束，但**显著提升模型理解**。

```json
{
  "type": "object",
  "properties": {
    "label": {
      "type": "string",
      "description": "Object class name, e.g. 'person', 'car', 'dog'"
    },
    "confidence": {
      "type": "number",
      "description": "Detection confidence between 0.0 and 1.0"
    }
  },
  "required": ["label", "confidence"],
  "additionalProperties": false
}
```

**为什么 `description` 很重要**：
- 模型的**注意力**会关注这些描述。
- 帮助模型理解**语义意图**（而不是只按类型生成）。
- 是"提示词"的一部分，但**结构化地**嵌入在 schema 里。

**写 description 的原则**：
- **简洁明确**：一句话说清楚。
- **给例子**：`e.g. 'person', 'car'`。
- **给范围**：`between 0.0 and 1.0`。
- **给单位**：`in pixels`、`in seconds`。
- **不要重复字段名**：`"label": {"description": "The label"}` 是废话。

### 4.6 `enum`：限定取值

`enum` 用于限定字段只能取特定的值：

```json
{
  "type": "object",
  "properties": {
    "sentiment": {
      "type": "string",
      "enum": ["positive", "negative", "neutral"],
      "description": "Sentiment polarity"
    }
  },
  "required": ["sentiment"],
  "additionalProperties": false
}
```

**作用**：
- 模型只能从这三个值中选一个。
- 保证下游代码不需要处理"未知值"。

**使用场景**：
- 分类任务
- 状态字段（`pending` / `done` / `failed`）
- 优先级（`low` / `medium` / `high`）

### 4.7 一个完整的例子

**任务**：从一段文字中提取人物信息。

**Schema**：

```json
{
  "type": "object",
  "properties": {
    "persons": {
      "type": "array",
      "description": "All persons mentioned in the text",
      "items": {
        "type": "object",
        "properties": {
          "name": {
            "type": "string",
            "description": "Full name of the person"
          },
          "age": {
            "type": "integer",
            "description": "Age in years, -1 if unknown"
          },
          "occupation": {
            "type": "string",
            "description": "Job title, empty string if unknown"
          },
          "nationality": {
            "type": "string",
            "description": "Country code like 'US', 'CN', 'FR'"
          }
        },
        "required": ["name", "age", "occupation", "nationality"],
        "additionalProperties": false
      }
    },
    "total_count": {
      "type": "integer",
      "description": "Total number of persons"
    }
  },
  "required": ["persons", "total_count"],
  "additionalProperties": false
}
```

**对应的合法输出**：

```json
{
  "persons": [
    {
      "name": "Albert Einstein",
      "age": -1,
      "occupation": "Physicist",
      "nationality": "DE"
    },
    {
      "name": "Marie Curie",
      "age": 66,
      "occupation": "Chemist",
      "nationality": "FR"
    }
  ],
  "total_count": 2
}
```

---

## 第五章 后端实现：LM Studio / Ollama / vLLM

### 5.1 后端角色的理解

在 Structured Output 的完整链路中，**后端**（LM Studio / Ollama / vLLM）扮演**约束执行者**的角色：

```
客户端 → [response_format] → 后端 → [约束解码] → 模型
```

**后端的职责**：
1. 接收 `response_format` 参数。
2. 把 JSON Schema 编译成状态机。
3. 在模型生成每个 token 时应用约束。
4. 把生成的 token 流返回给客户端。

**后端不负责**：
- 决定用不用 Structured Output（由客户端决定）。
- 修改 schema（原样使用）。
- 校验生成的最终 JSON（约束解码已经保证了）。

### 5.2 支持矩阵

| 后端 | 版本要求 | JSON Mode | Structured Outputs | 备注 |
|------|---------|:---------:|:------------------:|------|
| **OpenAI** | - | ✅ | ✅ | 规范制定者 |
| **LM Studio** | 0.3.0+ | ✅ | ✅ | 桌面端最常用 |
| **Ollama** | 0.3.0+ | ✅ | ✅ | 命令行/服务端 |
| **vLLM** | 0.6.0+ | ✅ | ✅ | 生产级部署 |
| **SGLang** | - | ✅ | ✅ | 高性能推理 |
| **TGI** | - | ✅ | ⚠️ 部分 | HuggingFace |
| **DeepSeek API** | - | ✅ | ✅ | 云端 |
| **Groq** | - | ✅ | ✅ | 云端 |

**⚠️ 版本很重要**：旧版本**不支持**或**支持不完整**。使用前先查后端文档。

### 5.3 LM Studio 的实现

LM Studio 是最常用的本地后端，也是 LingoFuse 项目推荐的后端。

**版本要求**：**0.3.0 或更高**。

**配置步骤**：
1. 启动 LM Studio。
2. 加载一个**支持 Structured Outputs 的模型**（见 5.4）。
3. 打开本地服务器（默认端口 `1234`）。
4. 客户端调用 `POST /v1/chat/completions`，携带 `response_format`。

**LM Studio 的约束解码实现**：
- 底层使用 **llama.cpp 的 GBNF grammar** 或 **Outlines** 库。
- 把 JSON Schema 转换为 grammar。
- 在 token 采样时应用 grammar 约束。

**已知限制**：
- `minimum` / `maximum` 关键字**可能被忽略**（模型会输出范围内的值，但不是硬约束）。
- 深层嵌套（>5 层）可能导致编译失败。
- `oneOf` / `allOf` / `not` 不支持。
- 复杂正则（`pattern`）可能不支持。

### 5.4 模型能力要求

⚠️ **关键点**：**不是所有模型都能用 Structured Outputs**。

**要求**：
1. **模型需支持 Function Calling 或 Structured Outputs**。
2. **参数量 ≥ 7B**（小于 7B 的模型通常无法稳定遵循 schema）。
3. **经过专门的训练或微调**（支持 JSON Schema 约束解码）。

**推荐模型**：

| 模型 | 参数量 | 多模态 | 备注 |
|------|:------:|:------:|------|
| **Qwen2.5 系列** | 7B+ | ❌ | 纯文本，Structured Outputs 表现优秀 |
| **Qwen2.5-VL 系列** | 7B+ | ✅ | 视觉语言模型，支持图片 + Structured Outputs |
| **Nemotron Omni** | 30B (激活 3B) | ✅ | LingoFuse 推荐模型 |
| **GPT-4o** | - | ✅ | OpenAI 云端 |
| **Claude 3.5** | - | ✅ | Anthropic 云端 |
| **DeepSeek-V3** | - | ❌ | 云端 |

**不推荐的模型**：
- **参数量 < 7B**：小模型无法稳定遵循复杂 schema。
- **纯文本补全模型**（如 GPT-3、早期的 LLaMA）：不支持 chat 格式。
- **未针对 Structured Outputs 训练的模型**：可能把 schema 当成普通文本。

**如何确认模型支持**：

最简单的方法：**用 curl 手工测一次**。

```bash
curl -N -X POST http://127.0.0.1:1234/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "qwen2.5-7b-instruct",
    "messages": [{"role": "user", "content": "What is 2+2?"}],
    "stream": false,
    "response_format": {
      "type": "json_schema",
      "json_schema": {
        "name": "math_answer",
        "strict": true,
        "schema": {
          "type": "object",
          "properties": {
            "answer": {"type": "integer"},
            "explanation": {"type": "string"}
          },
          "required": ["answer", "explanation"],
          "additionalProperties": false
        }
      }
    }
  }'
```

**预期结果**：返回一个合法的 JSON 对象。

**失败信号**：
- HTTP 400 / 422。
- 返回纯文本而不是 JSON。
- 返回 JSON 但字段名不对。

### 5.5 常见陷阱

**陷阱 1：模型没加载 / 加载错了**

**症状**：请求报错 `model not found`。

**修复**：确认 LM Studio 的 `/v1/models` 返回了你指定的模型 ID。

```bash
curl http://127.0.0.1:1234/v1/models
```

**陷阱 2：schema 用了不支持的关键字**

**症状**：HTTP 400，错误信息含糊（如 `invalid schema`）。

**修复**：
- 简化 schema。
- 移除 `oneOf` / `allOf` / `not` / `if`。
- 移除 `patternProperties`。

**陷阱 3：`additionalProperties: false` 忘了设**

**症状**：模型返回了 schema 之外的字段。

**修复**：每个 object 都加 `"additionalProperties": false`。

**陷阱 4：`required` 忘了设**

**症状**：模型省略了某些字段。

**修复**：所有必填字段都要列入 `required`。

**陷阱 5：JSON Schema 太复杂**

**症状**：编译失败或生成超时。

**修复**：
- 简化 schema。
- 把复杂 schema 拆成多次请求。
- 减少嵌套深度。

---

## 第六章 典型应用场景

### 6.1 目标检测（方框标注）

**任务**：检测图片中的所有目标，返回标签 + 方框坐标。

**Schema**：

```json
{
  "type": "object",
  "properties": {
    "detections": {
      "type": "array",
      "description": "List of detected objects",
      "items": {
        "type": "object",
        "properties": {
          "label": {
            "type": "string",
            "description": "Object class name, e.g. person, car, dog"
          },
          "bbox": {
            "type": "array",
            "description": "Normalized bbox [x_min, y_min, x_max, y_max], 0~1",
            "items": { "type": "number" },
            "minItems": 4,
            "maxItems": 4
          },
          "confidence": {
            "type": "number",
            "description": "Detection confidence 0.0~1.0"
          }
        },
        "required": ["label", "bbox", "confidence"],
        "additionalProperties": false
      }
    }
  },
  "required": ["detections"],
  "additionalProperties": false
}
```

**提示词配合**：

```
检测图片中的所有目标。返回归一化坐标（0~1），
格式为 [x_min, y_min, x_max, y_max]，
其中 (x_min, y_min) 是左上角，(x_max, y_max) 是右下角。
```

**预期输出**：

```json
{
  "detections": [
    {"label": "person", "bbox": [0.12, 0.23, 0.45, 0.78], "confidence": 0.95}
  ]
}
```

**关键点**：
- **坐标归一化到 `[0, 1]`**：避免分辨率依赖。
- **`bbox` 顺序**：`[x_min, y_min, x_max, y_max]`。
- **在提示词中明确坐标约定**。

### 6.2 信息抽取

**任务**：从简历文本中提取结构化信息。

**Schema**：

```json
{
  "type": "object",
  "properties": {
    "name": { "type": "string" },
    "email": { "type": "string" },
    "phone": { "type": "string" },
    "education": {
      "type": "array",
      "items": {
        "type": "object",
        "properties": {
          "school": { "type": "string" },
          "degree": { "type": "string", "enum": ["Bachelor", "Master", "PhD"] },
          "year": { "type": "integer" }
        },
        "required": ["school", "degree", "year"],
        "additionalProperties": false
      }
    },
    "skills": {
      "type": "array",
      "items": { "type": "string" }
    }
  },
  "required": ["name", "email", "phone", "education", "skills"],
  "additionalProperties": false
}
```

### 6.3 分类任务

**任务**：判断用户评论的情感倾向。

**Schema**：

```json
{
  "type": "object",
  "properties": {
    "sentiment": {
      "type": "string",
      "enum": ["positive", "negative", "neutral"]
    },
    "confidence": {
      "type": "number"
    },
    "keywords": {
      "type": "array",
      "items": { "type": "string" },
      "description": "Key phrases driving the sentiment"
    }
  },
  "required": ["sentiment", "confidence", "keywords"],
  "additionalProperties": false
}
```

### 6.4 OCR（文字识别）

**任务**：从图片中提取所有文字及其位置。

**Schema**：

```json
{
  "type": "object",
  "properties": {
    "texts": {
      "type": "array",
      "items": {
        "type": "object",
        "properties": {
          "content": { "type": "string" },
          "bbox": {
            "type": "array",
            "items": { "type": "number" },
            "minItems": 4,
            "maxItems": 4
          }
        },
        "required": ["content", "bbox"],
        "additionalProperties": false
      }
    }
  },
  "required": ["texts"],
  "additionalProperties": false
}
```

### 6.5 关键点检测

**任务**：检测人体关键点（头部、肩膀、肘部等）。

**Schema**：

```json
{
  "type": "object",
  "properties": {
    "keypoints": {
      "type": "array",
      "items": {
        "type": "object",
        "properties": {
          "name": {
            "type": "string",
            "enum": ["nose", "left_eye", "right_eye", "left_shoulder",
                     "right_shoulder", "left_elbow", "right_elbow",
                     "left_wrist", "right_wrist", "left_hip", "right_hip",
                     "left_knee", "right_knee", "left_ankle", "right_ankle"]
          },
          "x": { "type": "number" },
          "y": { "type": "number" },
          "visibility": { "type": "number" }
        },
        "required": ["name", "x", "y", "visibility"],
        "additionalProperties": false
      }
    }
  },
  "required": ["keypoints"],
  "additionalProperties": false
}
```

### 6.6 智能体决策

**任务**：让模型输出下一步应该执行的动作。

**Schema**：

```json
{
  "type": "object",
  "properties": {
    "action": {
      "type": "string",
      "enum": ["search", "read", "write", "finish"]
    },
    "parameters": {
      "type": "object",
      "properties": {
        "query": { "type": "string" },
        "file_path": { "type": "string" },
        "content": { "type": "string" }
      },
      "required": ["query", "file_path", "content"],
      "additionalProperties": false
    },
    "reasoning": {
      "type": "string",
      "description": "Why this action is chosen"
    }
  },
  "required": ["action", "parameters", "reasoning"],
  "additionalProperties": false
}
```

**注意**：如果使用 `tools` 功能（Function Calling），应该用 `tools` 参数而不是 `response_format`。

---

## 第七章 在 LingoFuse 生态中的实践

### 7.1 LingoFuse 的架构

**LingoFuse** 是一个跨语言 RPC 框架。在 LLM 生态中，它扮演**中间层**的角色：

```
┌──────────────────┐
│  Pascal 客户端    │  llm_client_v3.pas
└────────┬─────────┘
         │ LingoFuse RPC
         ↓
┌──────────────────┐
│  llm_proxy       │  转发层
│  或 llm_proxy_tool│
└────────┬─────────┘
         │ HTTP SSE
         ↓
┌──────────────────┐
│  LM Studio       │  推理后端
└──────────────────┘
```

**关键点**：
- **客户端不直接连后端**——所有请求经过 LingoFuse 代理层。
- **代理层是透明的**——原样转发 `response_format`。
- **后端决定是否支持**——LM Studio 支持 Structured Outputs。

### 7.2 完整调用链

**一次"图片检测"请求的完整链路**：

**步骤 1：Pascal 客户端发起请求**

```pascal
LLM.GenerateWithImageFileAndSchema(
  '检测图片中的所有目标',
  '',
  'photo.png',
  'object_detection',
  SchemaBody,
  True,      // strict
  S, E);
```

**步骤 2：客户端组装 JSON**

```json
{
  "content": "检测图片中的所有目标",
  "prompt": "",
  "client_name": "@__generate__@...",
  "attachments": [
    {
      "kind": "image",
      "name": "photo.png",
      "mime": "image/png",
      "data_b64": "..."
    }
  ],
  "options": {
    "response_format": {
      "type": "json_schema",
      "json_schema": {
        "name": "object_detection",
        "strict": true,
        "schema": { ... }
      }
    }
  }
}
```

**步骤 3：LingoFuse RPC 发送到代理**

客户端通过 `LF_CallEx` 把请求发给 `LLM_Service`。

**步骤 4：代理层转发到 LM Studio**

代理层（`llm_proxy` / `llm_proxy_tool`）：
1. 保留 `options.response_format`（白名单允许通过）。
2. 保留 `attachments`（转发图片）。
3. 通过 `OpenAIStreamClient.stream_chat` 转发到 LM Studio。

**步骤 5：LM Studio 约束解码**

LM Studio：
1. 把 `response_format.schema` 编译成状态机。
2. 加载视觉编码器（mmproj）处理图片。
3. 在生成每个 token 时应用 schema 约束。
4. 通过 SSE 流返回 token。

**步骤 6：代理层转发回客户端**

代理层：
1. 接收 SSE 流。
2. 提取 `content` 和 `reasoning_content`。
3. 通过 `LF_Sequenced_Notify` 推送给客户端。

**步骤 7：客户端解析**

客户端：
1. `OnChunk` 多次触发，累积完整 JSON。
2. `OnFinish` 触发，得到最终 JSON。
3. 用 `TZ_JsonObject.ParseText` 解析。

### 7.3 Pascal 客户端 API

SDK（`llm_client_v3.pas` v3.10）提供 4 个 Structured Output 入口：

| 方法 | 用途 |
|------|------|
| `GenerateStructured` | 完整 `response_format` JSON 字符串 |
| `GenerateWithJsonSchema` | schema 名 + 本体 + strict，无附件 |
| **`GenerateWithImageFileAndSchema`** | **图片文件 + schema（检测器推荐）** |
| `GenerateWithAttachmentsAndSchema` | 附件数组 + schema（完全控制） |

**推荐**：**图片检测场景用 `GenerateWithImageFileAndSchema`**，一步到位。

### 7.4 完整代码示例

```pascal
uses
  llm_client_v3, lingofuse_import, Z.Json, Z.PascalStrings;

const
  DETECTOR_SCHEMA_BODY =
    '{"type":"object","properties":{' +
    '"detections":{"type":"array","items":{' +
    '"type":"object","properties":{' +
    '"label":{"type":"string","description":"Object class name"},' +
    '"bbox":{"type":"array","description":"Normalized [x_min,y_min,x_max,y_max], 0~1",' +
    '"items":{"type":"number"},"minItems":4,"maxItems":4},' +
    '"confidence":{"type":"number","description":"0.0~1.0"}},' +
    '"required":["label","bbox","confidence"],' +
    '"additionalProperties":false}}},' +
    '"required":["detections"],"additionalProperties":false}';

procedure TForm1.DetectImage(const FilePath: string);
var
  S, E: string;
  SchemaBody: string;
begin
  SchemaBody := DETECTOR_SCHEMA_BODY;

  if not LLM.GenerateWithImageFileAndSchema(
    '检测图片中的所有目标。返回归一化坐标（0~1），格式为 [x_min, y_min, x_max, y_max]。',
    '',
    FilePath,
    'object_detection',
    SchemaBody,
    True,
    S, E) then
  begin
    ShowMessage('检测失败: ' + E);
    Exit;
  end;

  // 后续由 OnChunk / OnFinish 事件回调处理
end;

procedure TForm1.OnChunk(const SessionId, Text: string);
begin
  TThread.Queue(nil,
    procedure
    begin
      FAccum.Append(Text);
    end);
end;

procedure TForm1.OnFinish(const SessionId, Reason: string);
var
  J: TZ_JsonObject;
  D: TZ_JsonArray;
  I: integer;
begin
  TThread.Queue(nil,
    procedure
    var
      FullJson: string;
      Dets: TZ_JsonArray;
      I: integer;
      B: TZ_JsonArray;
    begin
      FullJson := FAccum.ToString;
      FAccum.Clear;

      J := TZ_JsonObject.Create;
      try
        if not J.ParseText(FullJson) then
        begin
          ShowMessage('返回的 JSON 无效');
          Exit;
        end;

        Dets := J.a['detections'];
        for I := 0 to Dets.Count - 1 do
        begin
          B := Dets.O[I].a['bbox'];
          Memo1.Lines.Add(Format('%s: [%.3f, %.3f, %.3f, %.3f]',
            [Dets.O[I].S['label'],
             B.F[0], B.F[1], B.F[2], B.F[3]]));
        end;
      finally
        J.Free;
      end;
    end);
end;
```

### 7.5 调试方法

**1. 打开代理层 DEBUG 日志**

启动 `llm_proxy` 时加 `--log-level DEBUG`：

```powershell
.\llm_proxy.exe `
  --backend-url http://127.0.0.1:1234/v1 `
  --backend-model "qwen2-vl-7b-instruct" `
  --vision `
  --log-level DEBUG
```

**观察关键日志**：

```
[DEBUG] Backend request: url=... model=... msgs=N tools=no response_format=yes
```

- `response_format=yes`：代理确实转发了。
- `response_format=no`：客户端没传或代理版本旧。

**2. 手工用 curl 测试后端**

```bash
curl -N -X POST http://127.0.0.1:1234/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "qwen2-vl-7b-instruct",
    "messages": [{
      "role": "user",
      "content": [
        {"type": "text", "text": "Describe this image as JSON."},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,..."}}
      ]
    }],
    "stream": false,
    "response_format": {
      "type": "json_schema",
      "json_schema": {
        "name": "image_description",
        "strict": true,
        "schema": {
          "type": "object",
          "properties": {
            "summary": {"type": "string"}
          },
          "required": ["summary"],
          "additionalProperties": false
        }
      }
    }
  }'
```

如果这个 curl 成功，说明后端支持。否则问题在后端。

**3. 检查能力矩阵**

```pascal
if LLM.ServerKind = 'service' then
  ShowMessage('llm_service 不支持 Structured Output')
else if LLM.ServerKind = 'proxy' then
  ShowMessage('代理服务端，Structured Output 可用');
```

---

## 第八章 最佳实践

### 8.1 Schema 设计原则

**原则 1：尽可能扁平**

❌ 避免：

```json
{
  "type": "object",
  "properties": {
    "result": {
      "type": "object",
      "properties": {
        "data": {
          "type": "object",
          "properties": {
            "items": { ... }
          }
        }
      }
    }
  }
}
```

✅ 推荐：

```json
{
  "type": "object",
  "properties": {
    "items": { ... }
  }
}
```

**为什么**：
- 深层嵌套降低模型准确率。
- 部分后端对深度有限制（通常 5 层）。
- 扁平结构更易理解和调试。

**原则 2：每个字段都给 `description`**

❌ 差：

```json
{"label": {"type": "string"}}
```

✅ 好：

```json
{
  "label": {
    "type": "string",
    "description": "Object class name, e.g. 'person', 'car', 'dog'"
  }
}
```

**原则 3：`required` 要完整**

所有必需字段都要列入 `required`。

**原则 4：`additionalProperties: false` 是必须的**

严格模式下，**每个 object** 都要：

```json
{
  "type": "object",
  "properties": { ... },
  "required": [ ... ],
  "additionalProperties": false
}
```

**原则 5：用 `enum` 限定分类字段**

分类字段（如情感、状态、优先级）用 `enum`：

```json
{
  "sentiment": {
    "type": "string",
    "enum": ["positive", "negative", "neutral"]
  }
}
```

**原则 6：不要依赖 `minimum` / `maximum` 做硬约束**

某些后端**可能忽略** `minimum` / `maximum`。用 `description` 补充说明：

```json
{
  "confidence": {
    "type": "number",
    "minimum": 0,
    "maximum": 1,
    "description": "Confidence score. MUST be between 0.0 and 1.0."
  }
}
```

### 8.2 提示词配合

**原则 1：明确说明期望**

即使有 schema，提示词也应该说明任务。

❌ 差：

```
用户：检测图片。
```

✅ 好：

```
用户：检测图片中的所有目标。返回归一化坐标（0~1），
      格式为 [x_min, y_min, x_max, y_max]。
```

**原则 2：强调关键约定**

如果 schema 里没有明确约束的（如坐标顺序），在提示词里强调：

```
bbox 格式说明：
  - 归一化坐标，取值范围 [0, 1]
  - [x_min, y_min, x_max, y_max]
  - (x_min, y_min) = 左上角
  - (x_max, y_max) = 右下角
```

**原则 3：不要和 schema 冲突**

❌ 冲突：

```
Schema 要求 { "detections": [...] }
提示词却说：请直接返回方框列表 [...]   ← 冲突
```

✅ 一致：

```
Schema 要求 { "detections": [...] }
提示词也说：请返回包含 detections 数组的 JSON
```

### 8.3 错误处理

**错误类型 1：后端不支持**

```pascal
// 客户端侧检查
if LLM.ServerKind = 'service' then
begin
  ShowMessage('llm_service 不支持 Structured Output');
  ShowMessage('请使用 llm_proxy / llm_proxy_tool');
  Exit;
end;
```

**错误类型 2：模型不遵循 schema**

即使有 Structured Output，某些模型可能：
- 忽略 `description`。
- 输出不在 `enum` 中的值。
- 坐标格式错误。

**修复**：
1. 换更强的模型。
2. 在提示词中重复关键约定。
3. 在客户端做**事后校验**和**修正**。

**客户端校验示例**：

```pascal
function ValidateBBox(const BBox: TZ_JsonArray): boolean;
var
  I: integer;
  V: double;
begin
  Result := False;
  if BBox.Count <> 4 then Exit;
  for I := 0 to 3 do
  begin
    V := BBox.F[I];
    if (V < 0) or (V > 1) then Exit;
  end;
  Result := True;
end;
```

**错误类型 3：返回的 JSON 解析失败**

理论上不应该发生（Structured Output 保证合法 JSON）。但如果发生：

```pascal
if not J.ParseText(FullJson) then
begin
  DoStatus('[ERROR] Invalid JSON: ' + FullJson);
  // 记录日志，做分析
  Exit;
end;
```

### 8.4 性能考量

**性能 1：Structured Output 会增加多少延迟？**

**几乎不增加**。约束解码的开销主要来自：
- schema 编译（**一次**，之后缓存）。
- token 掩码（每步 O(V)，V 是词汇表大小）。

掩码操作相比模型推理本身**可以忽略**。

**性能 2：schema 越简单越好**

- 编译时间：简单 schema 更快。
- 生成时间：简单 schema 的 token 空间更大，模型有更多选择。

**性能 3：流式输出仍然有效**

Structured Output **不影响流式输出**。token 一个一个来，客户端可以累积。

**性能 4：图片处理是瓶颈**

对于多模态任务（图片检测），**图片编码**才是主要耗时。

- 一张 1080p 图片可能需要 1-2 秒编码。
- 大图片（4K）更慢。
- **建议**：压缩图片到合理尺寸（长边 ≤ 1024px）。

---

## 第九章 与其他方案的对比

### 9.1 vs Function Calling

**Function Calling** 也是一种结构化输出，但目标不同。

| 维度 | Structured Outputs | Function Calling |
|------|-------------------|-----------------|
| **目的** | 让模型输出结构化**数据** | 让模型调用**外部函数** |
| **API 字段** | `response_format` | `tools` / `tool_calls` |
| **输出** | 直接返回 JSON 数据 | 返回函数名 + 参数 |
| **客户端参与** | 不参与 | 需要执行函数并返回结果 |
| **多轮** | 无 | 支持（多轮调用） |

**选择**：
- **只要模型返回结构化数据**：用 Structured Outputs。
- **需要模型调用外部 API / 工具**：用 Function Calling。
- **两者结合**：某些场景可以同时使用（如模型先思考输出 JSON，再决定调用哪个函数）。

### 9.2 vs 提示词工程

| 维度 | 提示词工程 | Structured Outputs |
|------|:---------:|:-----------------:|
| **成功率** | 40-85% | 100% |
| **实现复杂度** | 低 | 低 |
| **依赖模型能力** | 高 | 低 |
| **可调试性** | 差 | 好 |
| **字段名一致** | ❌ | ✅ |

**结论**：**只要后端支持，永远用 Structured Outputs**。提示词工程只在极端情况下（如后端不支持）保留。

### 9.3 vs 事后解析

| 维度 | 事后解析 | Structured Outputs |
|------|:-------:|:-----------------:|
| **成功率** | 60-90% | 100% |
| **实现复杂度** | 高 | 低 |
| **维护成本** | 高（永远在打补丁） | 低 |
| **性能** | 中 | 高 |

**结论**：**事后解析应该被淘汰**。所有需要结构化输出的场景都应该用 Structured Outputs。

### 9.4 vs 微调

**微调（Fine-tuning）** 是另一种让模型输出特定格式的方法。

| 维度 | 微调 | Structured Outputs |
|------|:---:|:-----------------:|
| **成本** | 高（需要数据和算力） | 低（零成本） |
| **灵活性** | 低（改 schema 要重训） | 高（随时改） |
| **可靠性** | 中 | 高 |
| **适用场景** | 特定领域任务 | 通用结构化输出 |

**结论**：
- **一般场景**：用 Structured Outputs。
- **特殊领域**（如医疗、法律）：可以用微调 + Structured Outputs 组合。

---

## 第十章 未来展望

### 10.1 可能的演进方向

**方向 1：更复杂的 schema 支持**

目前不支持 `oneOf` / `allOf` / `not` / `if-then-else`。未来可能：
- 支持所有 JSON Schema 关键字。
- 支持更复杂的递归。

**方向 2：更高效的约束解码**

目前的约束解码每步 O(V)。未来可能：
- 通过状态机预编译优化。
- 增量 token 掩码更新。

**方向 3：更丰富的输出类型**

- XML（部分已有支持）。
- YAML。
- Protocol Buffers。
- 自定义 DSL。

**方向 4：与 Agent 框架深度集成**

- 多步任务的中间输出约束。
- 状态机驱动的 agent 决策。

### 10.2 对开发者的影响

**现在**：
- 所有需要 JSON 输出的场景用 Structured Outputs。
- 后端必须支持（LM Studio 0.3+、Ollama 0.3+、vLLM 0.6+）。
- 客户端只需传 schema。

**未来**：
- Structured Outputs 会成为**默认行为**。
- 复杂 schema 支持会更完善。
- 可能取代大部分 Function Calling 场景。

### 10.3 学习建议

**入门**：
1. 理解 JSON Schema（4 章）。
2. 手工用 curl 测一次（5.4）。
3. 在 Pascal 项目里用 `GenerateWithJsonSchema` 做第一个检测器。

**进阶**：
1. 学习 OpenAI 规范（3 章）。
2. 研究不同后端的实现差异（5 章）。
3. 掌握 schema 设计原则（8.1）。

**专家**：
1. 研究约束解码原理（2.3）。
2. 参与开源项目（Outlines、llama.cpp）。
3. 探索新的应用场景。

---

## 附录

### 附录 A：JSON Schema 速查表

**类型**：

| 关键字 | 值 |
|--------|-----|
| `type` | `"string"` / `"number"` / `"integer"` / `"boolean"` / `"object"` / `"array"` / `"null"` |

**对象**：

| 关键字 | 说明 |
|--------|------|
| `properties` | 字段定义 |
| `required` | 必填字段列表 |
| `additionalProperties` | `false` 禁止额外字段 |

**数组**：

| 关键字 | 说明 |
|--------|------|
| `items` | 元素 schema |
| `minItems` | 最少元素数（部分后端） |
| `maxItems` | 最多元素数（部分后端） |

**字符串**：

| 关键字 | 说明 |
|--------|------|
| `enum` | 限定取值 |
| `minLength` | 最小长度（部分后端） |
| `maxLength` | 最大长度（部分后端） |
| `pattern` | 正则（部分后端） |

**数字**：

| 关键字 | 说明 |
|--------|------|
| `minimum` | 最小值（部分后端可能忽略） |
| `maximum` | 最大值（部分后端可能忽略） |

**通用**：

| 关键字 | 说明 |
|--------|------|
| `description` | 描述（强烈推荐） |
| `default` | 默认值 |
| `$ref` | 引用定义（部分支持） |

### 附录 B：常见错误代码

**HTTP 400 / 422**：

| 错误信息 | 原因 | 修复 |
|---------|------|------|
| `unrecognized type json_schema` | schema 嵌套错误 | 检查是否多了一层 `json_schema` |
| `invalid schema` | schema 语法错误 | 用 JSON 校验工具检查 |
| `unsupported keyword: oneOf` | 用了不支持的 schema 关键字 | 移除或替代 |
| `strict mode requires additionalProperties` | 缺少 `additionalProperties: false` | 每个 object 都加上 |

**HTTP 404**：

| 错误信息 | 原因 | 修复 |
|---------|------|------|
| `model not found` | 指定的模型不存在 | 用 `/v1/models` 查询 |

**其他**：

| 症状 | 原因 | 修复 |
|------|------|------|
| 返回纯文本而不是 JSON | 后端不支持 Structured Outputs | 升级后端版本 |
| JSON 合法但字段名不对 | `strict` 为 `false` 或模型不遵循 | 设 `strict: true` |
| 返回被截断 | `max_tokens` 太小 | 增大 `max_tokens` |
| 生成时间极长 | schema 太复杂 | 简化 schema |

### 附录 C：参考资源

**官方文档**：

- [OpenAI Structured Outputs](https://platform.openai.com/docs/guides/structured-outputs)
- [LM Studio Structured Output](https://lmstudio.ai/docs/advanced/structured-output)
- [Ollama Structured Outputs](https://github.com/ollama/ollama/blob/main/docs/api.md#structured-outputs)
- [vLLM Structured Output](https://docs.vllm.ai/en/latest/features/structured_outputs.html)

**JSON Schema 资源**：

- [JSON Schema 官网](https://json-schema.org/)
- [JSON Schema 入门](https://json-schema.org/learn/getting-started-step-by-step)
- [Understanding JSON Schema](https://json-schema.org/understanding-json-schema/)

**相关库**：

- [Outlines](https://github.com/dottxt-ai/outlines)：约束解码库
- [llama.cpp GBNF](https://github.com/ggerganov/llama.cpp/blob/master/grammars/README.md)：语法约束

**本项目文档**：

- [`llm_client_v3.md`](llm_client_v3.md) §16 —— Pascal 客户端完整指南
- [`LingoFuse_LLM_Proxy_CLI_Guide.md`](LingoFuse_LLM_Proxy_CLI_Guide.md) §5.5 —— 代理层多模态转发
- [`LingoFuse_LLM_Proxy_Tool_CLI_Guide.md`](LingoFuse_LLM_Proxy_Tool_CLI_Guide.md) §4.5 —— LTB 多模态转发
- [`LingoFuse_Pascal_Complete_Guide.md`](LingoFuse_Pascal_Complete_Guide.md) —— Pascal 核心层完整指南（含踩坑知识库）

### 代码生成器

> ⚠️ **MCP-API 代码生成工具已独立到专用仓库：**
>
> ### 👉 [https://github.com/PassByYou888/LingoFuse-Tools](https://github.com/PassByYou888/LingoFuse-Tools)
>
> 一份声明 → **几十种目标语言的 API 接口**。声明规范、使用手册、生成器源码与预编译包均以该仓库为准。

---

**文档版本**：v1.1（目录对齐版——移除对已删除文档 `LingoFuse_LLM_Pitfalls_For_AI.md` 的引用；§7 引用改为指向实际存在的文档；新增 [`LingoFuse_Pascal_Complete_Guide.md`](LingoFuse_Pascal_Complete_Guide.md) 引用；MCP-API 生成器统一指向 [LingoFuse-Tools](https://github.com/PassByYou888/LingoFuse-Tools)；对齐实际仓库文档清单）

**维护建议**：如果发现新的 schema 关键字支持、新的后端、新的应用场景，追加到对应章节。

**核心结论**：

> **Structured Output 是 LLM 输出可靠性的重大突破。**
>
> **它用 schema 换掉了脆弱的提示词工程和事后解析，**
> **让模型输出的 JSON 100% 符合预期结构。**
>
> **如果你还没用，现在就是最好的开始时机。**