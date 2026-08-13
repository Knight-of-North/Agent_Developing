# Agent 项目模板

三人协作开发一个智能体（Agent）的标准骨架：自然语言提问 → 基于知识库精准回答并给出处。

## 技术栈

- **编排**：LangGraph（StateGraph 图编排）
- **检索**：LangChain Retrieval 全家桶（加载 → 切块 → Embedding → 向量库 → 生成）
- **模型**：DeepSeek（langchain-deepseek，API key 配在 `.env`，见 `.env.example`）
- **界面**：Gradio / Streamlit（后续）

## 目录结构

```
├── main.py          # 入口：python main.py
├── INTERFACE.md     # ⚠️ 接口契约（开发前必读）
├── agent/           # A：核心 Agent（LangGraph 编排、记忆）
├── rag/             # B：RAG 管道（retrieve + 建库脚本）
└── tools/           # C：工具注册、MCP、界面
```

## 环境初始化（每人本地执行一次）

```bash
conda create -n py10 python=3.10 -y
conda activate py10
pip install -r requirements.txt
```

## 运行

```bash
python main.py
```

## 团队协作约定（必读）

### 分支模型

```
main（稳定版，答辩交付）← dev（集成分支，联调地）← feature/xxx（每人一条）
```

### 初始化命令（已建好仓库时）

```bash
git clone <仓库地址>
git switch dev                      # 切到开发分支（本地自动创建）
git switch -c feature/你的模块名     # 从 dev 拉自己的分支
git push -u origin feature/你的模块名
```

### 日常节奏

| 时间 | 动作 |
|---|---|
| 开工 | `git switch dev && git pull`，再切回自己的分支 |
| 开发中 | 在自己的 feature 分支上 add + commit |
| 收工 | push 到自己的 feature 分支 |
| 每 2 天 | 把自己的分支合并进 dev（切到 dev → merge） |

### 合并操作（VSCode 图形化）

1. 左下角切到**目标分支**（要合进 dev 就切 dev）
2. 点源代码管理面板 `···` → Branch → **Merge Branch...**
3. 选**来源分支**（你的 feature/xxx）→ 解决冲突（如遇）→ 提交 → 同步 push

> 原则：**要合进谁，先站到谁。** 别在 main 上直接开发、别直接往 main push（交付前由 A 统一合并）。

### git 应急命令（GUI 报错时用）

```bash
git status            # 看状态（任何操作前先敲）
git log --oneline     # 看历史
git pull              # 拉取（同步按钮报错时手动拉）
git merge 分支名       # GUI 合并失败时兜底
git reset --hard HEAD~1  # 撤销最后一次提交（慎用）
```

## 开发顺序建议

1. **第 1 周**：各人先跑通自己的模块桩函数（能 import、能返回空结果）
2. **第 2 周**：A 接 LangGraph 编排 + 模型；B 建知识库 + 真实检索；C 写工具 + 界面
3. **第 3 周**：联调 + 打磨 + 答辩准备

> 接口签名见 `INTERFACE.md`，改接口必须同步更新文档并通知全组。
