# 保留 Provider 批量交付语义

Type: task
Status: resolved

## Answer

真实生产日志确认短时多任务会同时占用 Worker，并在三分钟创建期截止后才返回付费图片。现改为批次内逐张 `n=1` 请求、同账户同路由跨 Worker 串行，并将包含排队时间的总期限扩展到二十分钟；仍按实际交付数量结算。
