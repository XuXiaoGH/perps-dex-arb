# Backpack-Lighter 价差市价套利系统

## 概述

这是一个跨交易所套利机器人，同时监控 Backpack 和 Lighter 两个交易所的价格，当价差超过阈值时执行市价套利交易。

## 策略原理

```
┌─────────────────────────────────────────────────────────────────┐
│                     价差套利策略                                  │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  监控两个交易所的 BBO (Best Bid/Offer):                          │
│                                                                 │
│  Long Backpack 机会:                                            │
│    当 Lighter Bid > Backpack Ask + threshold                    │
│    → 在 Backpack 买入 (市价单)                                   │
│    → 在 Lighter 卖出 (市价单)                                    │
│    → 赚取价差利润                                                │
│                                                                 │
│  Short Backpack 机会:                                           │
│    当 Backpack Bid > Lighter Ask + threshold                    │
│    → 在 Lighter 买入 (市价单)                                    │
│    → 在 Backpack 卖出 (市价单)                                   │
│    → 赚取价差利润                                                │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

## 项目结构

```
backpack-lighter-arb/
├── arbitrage.py              # 入口文件
├── requirements.txt          # Python 依赖
├── env_example.txt           # 环境变量示例
├── README.md                 # 本文档
├── exchanges/                # 交易所客户端
│   ├── __init__.py
│   ├── base.py               # 基类定义
│   ├── backpack.py           # Backpack 客户端 + WebSocket
│   └── lighter.py            # Lighter 客户端 + WebSocket
└── strategy/                 # 策略组件
    ├── __init__.py
    ├── backpack_lighter_arb.py   # 核心套利策略
    ├── order_book_manager.py     # 订单簿管理
    ├── position_tracker.py       # 仓位跟踪
    └── data_logger.py            # 数据日志
```

## 快速开始

### 1. 安装依赖

```bash
cd backpack-lighter-arb
pip install -r requirements.txt
```

### 2. 配置环境变量

```bash
cp env_example.txt .env
# 编辑 .env 文件，填入你的 API 凭证
```

### 3. 运行机器人

```bash
# 基本用法 - BTC 套利
python arbitrage.py --ticker BTC --size 0.001

# 自定义阈值
python arbitrage.py --ticker ETH --size 0.01 --long-threshold 3 --short-threshold 3

# 带仓位限制
python arbitrage.py --ticker BTC --size 0.001 --max-position 0.1
```

## 命令行参数

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `--ticker` | 交易对 (BTC, ETH 等) | BTC |
| `--size` | 每次交易数量 | 必填 |
| `--long-threshold` | Long Backpack 价差阈值 | 5 |
| `--short-threshold` | Short Backpack 价差阈值 | 5 |
| `--max-position` | 最大单边仓位 (0=无限制) | 0 |
| `--check-interval` | 价差检查间隔 (秒) | 0.1 |

## 环境变量

| 变量 | 说明 |
|------|------|
| `BACKPACK_PUBLIC_KEY` | Backpack API 公钥 |
| `BACKPACK_SECRET_KEY` | Backpack API 私钥 (base64) |
| `API_KEY_PRIVATE_KEY` | Lighter 私钥 |
| `LIGHTER_ACCOUNT_INDEX` | Lighter 账户索引 |
| `LIGHTER_API_KEY_INDEX` | Lighter API 密钥索引 |

## 日志输出

运行时会在 `logs/` 目录生成以下文件：

- `backpack_lighter_{ticker}.log` - 交易日志
- `bbo_{ticker}_{date}.csv` - BBO 数据记录
- `trades_{ticker}_{date}.csv` - 交易执行记录

## 风险提示

⚠️ **重要提醒**

1. **测试先行**: 先用小仓位在测试环境运行
2. **API 权限**: 确保 API 有足够的交易权限
3. **资金准备**: 两个交易所都需要充足的保证金
4. **网络延迟**: 套利对延迟敏感，建议使用低延迟网络
5. **风险控制**: 建议设置 `--max-position` 限制最大敞口

## 架构设计

### 数据流

```
┌──────────────────┐    ┌──────────────────┐
│  Backpack WS     │    │   Lighter WS     │
│  Order Book      │    │   Order Book     │
└────────┬─────────┘    └────────┬─────────┘
         │                       │
         ▼                       ▼
    ┌────────────────────────────────┐
    │      OrderBookManager          │
    │   - 维护 BBO                   │
    │   - 计算价差                    │
    └────────────────┬───────────────┘
                     │
                     ▼
    ┌────────────────────────────────┐
    │     BackpackLighterArb         │
    │   - 检测套利机会                │
    │   - 执行市价单                  │
    │   - 仓位管理                    │
    └────────────────┬───────────────┘
                     │
         ┌───────────┴───────────┐
         ▼                       ▼
┌──────────────────┐    ┌──────────────────┐
│  Backpack        │    │   Lighter        │
│  Market Order    │    │   Market Order   │
└──────────────────┘    └──────────────────┘
```

### 主要组件

- **BackpackClient** / **LighterClient**: 交易所 API 封装
- **WebSocketManager**: 实时订单簿更新
- **OrderBookManager**: 订单簿状态管理和价差计算
- **PositionTracker**: 跨交易所仓位跟踪
- **DataLogger**: 交易和 BBO 数据记录

## 开发扩展

### 添加新的交易所

1. 在 `exchanges/` 目录创建新的客户端文件
2. 继承 `BaseExchangeClient` 类
3. 实现所有抽象方法
4. 在策略中添加新的交易所支持

### 自定义策略

修改 `strategy/backpack_lighter_arb.py` 中的 `trading_loop` 方法来实现自定义的套利逻辑。

## License

MIT License

