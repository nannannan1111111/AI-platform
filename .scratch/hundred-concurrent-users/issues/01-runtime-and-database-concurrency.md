# 生产运行时与数据库并发安全

Type: task
Status: resolved

## Scope

实现连接池预算、多 Web worker、集群单例定时任务、账户提交串行化和数据库就绪探针。

## Answer

已实现可配置连接池、默认 4 Web worker、PostgreSQL advisory lock 集群互斥、账户提交事务锁和 `/readyz`。
