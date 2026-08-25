# 数字输入类型统一与乐云图标

Type: task
Status: claimed

## Scope

修复数字输入导致的金额/额度字符串接口校验错误；接入乐云图标资源并更新 Web 外壳与画布页面 favicon。

## Evidence

Vue `v-model` 对 `type="number"` 自动产生 JavaScript number，而后端金额/额度字段声明为 `str`，导致 Pydantic 拒绝请求。

## Changes

- 前端所有相关金额/额度请求显式 `String(...)`。
- 后端请求模型通过 `DecimalInput` 兼容 string/number 并归一化为字符串。
- 使用用户提供的图标资源作为 `static/images/leyun-logo.png`。
