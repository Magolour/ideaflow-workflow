# 协作方式

1. 在 Issue 写清模块、目标、验收标准及依赖，领取后说明负责人。
2. 从最新 main 建分支，例如 feat/requirements-spec、feat/orchestration、feat/visualization。
3. 日常只改自己模块；跨模块接口先提出变更并邀请相关负责人评审。
4. 提交前运行模块所需检查，确认没有 .env、密钥、虚拟环境或生成产物。
5. 推送分支并提交 PR，写明变化、验证方法和已知限制；建议另一位成员评审后合并。
6. main 保持可演示，不直接推送。此为团队约定，尚未配置 GitHub 强制分支保护。

不要求使用同一种 IDE 或 Agent。协作依据仓库文件、Issue、PR 和接口契约。
