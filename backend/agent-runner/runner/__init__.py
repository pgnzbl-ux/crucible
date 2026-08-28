# runner 包入口。必须放在镜像 /app/runner（不可放 /workspace/runner）：
# host_workdir bind mount 到 /workspace 会盖掉镜像文件，导致 ModuleNotFoundError: runner。
