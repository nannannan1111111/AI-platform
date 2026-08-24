# 智能画布唯一入口

Type: task
Status: resolved

## Answer

新建画布接口和两个前端创建入口统一写入 `smart`。列表保留 classic 历史数据但标记为停用，不再提供经典编辑入口；`/classic` 旧地址由 Web 路由返回智能画布壳。LLM 设置、生成任务来源和画布跳转文案不再宣传经典画布。
