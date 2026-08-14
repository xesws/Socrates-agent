# Git 默认动作

- 对本仓库的改动，**默认 commit 并 push 到 `origin` 当前分支**，不要只留在工作区。例外（不要提交）：`.env`、密钥、本地草稿、用户未要求纳入版本库的杂文件。用户明确说「先别提交 / 先别推」时才停下。
- 每一个小的改动、每一个小的 feature 或者 bug fix，都需要写出明确的版本号以及准确的描述，并写到 message 里面。这样子我们才能够区分各个 commit 的版本。默认每开发一个 feature 或者 fix 一个小的 bug，都必须要 commit 加 push
- 每次在开发一个新的 feature 或者做 bug fix 之前，必须把当前版本的 plan 写到 docs 文件夹里面，并且文件名要清晰地体现出当前版本的编号
  
# Agent 集群开发
尽量使用 Agent 集群进行开发


# 参考案例
SWE-Agent通关手册v2.md