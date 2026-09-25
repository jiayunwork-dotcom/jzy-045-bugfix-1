# 酶促动力学计算服务（enzyme-kinetics）

一个长期在线的 HTTP 计算端点：给定酶动力学常数与底物浓度，按米氏方程返回反应速率；
在**竞争性抑制**条件下返回表观动力学常数。纯 JSON 接口，无网页界面、无登录/订单等外围逻辑。

- 运行时：**Python 3.12**（锁定）+ **Flask**（生产用 waitress 单进程多线程托管）
- 持久化：具名酶参数档落地 JSON 文件，重启不丢，卷挂载即可跨容器保留
- 并发：计算为无共享纯函数；酶仓库以锁串行化 + 原子文件替换，并发登记互不覆盖

## 动力学约定

无抑制，米氏方程：

```
v = Vmax · [S] / ([S] + Km)
饱和分数 = v / Vmax = [S] / ([S] + Km)
```

竞争性抑制（本服务唯一支持的抑制语义）：

```
factor α = 1 + [I]/Ki
Km_app   = Km · α        （表观米氏常数被抬高）
Vmax_app = Vmax          （最大速率不变）
v = Vmax · [S] / ([S] + Km_app)
```

Lineweaver–Burk 双倒数锚点：`1/v = (Km/Vmax)·(1/[S]) + 1/Vmax`。
竞争性抑制下纵截距 `1/Vmax` 不变、斜率 `Km_app/Vmax` 随 [I] 增大而增大。
抑制速率复用同一米氏核心（只换入 Km_app），从结构上保证该不变量不可能被做坏。

**矛盾参数挡回策略**：声明 `inhibition_type="competitive"` 却同时传入非竞争性/混合型
因子（`alpha`、`beta`），或 `vmax_factor` 不等于 1，或自报 `factor` 与 `1+[I]/Ki`
不一致，服务一律以 `422 inhibition_conflict` 带原因挡回，不猜测调用意图。

## 预置示范酶：hexokinase（己糖激酶）

生理浓度量级、可手算复核：`Vmax=10`、`Km=0.1 mM`、`Ki=0.05 mM`。

- `[S]=0.1`（=Km）→ `v=5`（半饱和点）
- `[I]=Ki=0.05` → `factor=2`、`Km_app=0.2`、`Vmax_app=10`
- 此时 `[S]=0.2`（=Km_app）→ `v=5`（半饱和点移向高底物侧）

## 接口

所有端点只收发 `application/json`。错误响应统一形如：

```json
{ "error": "invalid_parameter", "message": "中文原因……", "field": "km" }
```

### 健康检查

`GET /health` → `200 {"status":"ok", ...}`

### 1. 无抑制速率

`POST /rate`

请求字段（常数二选一：整体点名酶，或整体内联，不可混用）：

| 字段 | 类型 | 约束 |
|---|---|---|
| `enzyme` | string | 已登记酶名（与 vmax/km 互斥） |
| `vmax` | number | 必须 > 0 |
| `km` | number | 必须 > 0 |
| `substrate` | number | 必须 ≥ 0（=0 是合法结果，速率为 0，不报错） |

```bash
curl -s -X POST localhost:8080/rate \
  -H 'Content-Type: application/json' \
  -d '{"vmax":10,"km":0.1,"substrate":0.3}'
# {"vmax":10.0,"km":0.1,"substrate":0.3,"rate":7.5,
#  "saturation_fraction":0.75,"constants_source":"inline"}
```

### 2. 竞争性抑制下的速率与表观常数

`POST /rate/inhibited`

在第一类字段基础上追加：

| 字段 | 类型 | 约束 |
|---|---|---|
| `inhibitor` `[I]` | number | 必须 ≥ 0（=0 连续退回无抑制） |
| `ki` | number | 必须 > 0；请求未给时回退到酶参数档内的 `ki` |
| `inhibition_type` | string | 只接受 `"competitive"` |
| `factor` | number | 可选；自报抑制因子，必须与 `1+[I]/Ki` 一致 |
| `vmax_factor` | number | 可选；竞争性下只接受恰好 1 |

```bash
curl -s -X POST localhost:8080/rate/inhibited \
  -H 'Content-Type: application/json' \
  -d '{"enzyme":"hexokinase","substrate":0.2,"inhibitor":0.05,
       "inhibition_type":"competitive"}'
# {"vmax":10.0,"km":0.1,"substrate":0.2,"inhibitor":0.05,"ki":0.05,
#  "inhibition_type":"competitive","factor":2.0,
#  "apparent_km":0.2,"apparent_vmax":10.0,
#  "rate":5.0,"saturation_fraction":0.5,
#  "constants_source":"enzyme:hexokinase"}
```

### 3. 酶参数档登记与取用

| 方法 & 路径 | 行为 |
|---|---|
| `GET /enzymes` | 列出全部参数档 |
| `POST /enzymes` | 登记：`{"name","vmax","km",?ki,?description}`；重名 → `409` |
| `GET /enzymes/<name>` | 按名取用；不存在 → `404` |
| `PUT /enzymes/<name>` | 覆盖登记（新建返回 201，覆盖返回 200；未给的 `ki`/`description` 沿用旧值） |
| `DELETE /enzymes/<name>` | 删除；不存在 → `404` |

`name`：1–64 字符，字母/数字开头，仅含字母数字、`_`、`.`、`-`。
存储文件位置由环境变量 `ENZYME_STORE_PATH` 控制（容器默认 `/data/enzymes.json`）。

非法输入与状态码：参数问题 `400 invalid_parameter`；抑制语义矛盾
`422 inhibition_conflict`；重名 `409 enzyme_exists`；未找到 `404 enzyme_not_found`；
存储不可用 `500 store_error`。NaN/无穷大、布尔值冒充数字、未知字段、空体/非 JSON
对象请求体同样被 400 拒绝。

## 目录结构（按职责拆模块）

```
app/
  __init__.py        包导出
  errors.py          业务异常与错误码
  validation.py      入参校验（正数/非负/有限数/枚举/必填/白名单）
  kinetics.py        米氏速率核心 + 饱和分数 + Lineweaver–Burk
  inhibition.py      竞争性抑制换算 + 矛盾因子挡回
  enzyme_store.py    酶参数档持久化（JSON、原子写、锁、预置己糖激酶）
  routes.py          Flask 蓝图、应用工厂、错误处理器
wsgi.py              waitress 入口（--call wsgi:app）
tests/               pytest：核心/抑制/校验/仓库/HTTP/真实并发
Dockerfile           python:3.12-slim，非 root，/data 卷，healthcheck
docker-compose.yml   一键构建启动 + 命名卷持久化
```

## 容器构建与运行

```bash
docker compose up --build
# 或
docker build -t enzyme-kinetics .
docker run -d -p 8080:8080 -v enzyme-data:/data enzyme-kinetics
```

健康检查通过后即可调用；停止重启容器，已登记的酶仍在 `/data` 卷中。

## 本地开发与测试

```bash
python3.12 -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
pytest                 # 84 个测试（含真实 socket 并发护栏）
ENZYME_STORE_PATH=./data/enzymes.json flask --app wsgi run
```

测试重点（回归护栏）：半饱和点 `v=Vmax/2`；竞争性下 `Vmax_app` 恒定、
高底物仍逼近同一 Vmax、半饱和点随 [I] 右移；Vmax 翻倍任意点速率翻倍；
`[I]=0` 与 Ki→∞ 连续退回无抑制；`[S]=0` 合法为零；负/零/非有限非法输入逐条件覆盖。
