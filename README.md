# A股利好监控系统

从同花顺抓取7x24新闻，做情绪分析，生成暗色主题监控面板，并通过 Telegram Bot 推送利好预警。

## 功能

- **Hot7 热点股** — 新闻提及最多、情绪评分最高的7只A股
- **实时利好精选** — 综合评分排序 (新闻影响 × 时效衰减 × 弹性)，S/A/B/C 四级
- **热门板块 & ETF** — 板块提及频次 + 评分柱状图 + 关联ETF代码
- **新闻流** — 支持全部/利好/利空筛选
- **Telegram Bot** — "发送"指令即时汇总 + 盘中15分钟自动推送 + S级利好预警

## 技术栈

- Python 3 (仅依赖 requests)
- HTML/CSS/JS 单文件面板 (TailwindCSS + ECharts)
- 同花顺新闻 API + 腾讯行情/K线 API
- Telegram Bot API

## 运行方式

```bash
# 安装依赖
pip install requests

# 生成面板
python3 monitor.py

# 生成面板 + 推送 Telegram
python3 monitor.py --push

# Bot模式 (命令监听 + 定时推送 + 预警)
python3 monitor.py --bot
```

## 环境变量

| 变量 | 说明 |
|------|------|
| `TG_TOKEN` | Telegram Bot Token |
| `TG_CHAT_ID` | 私聊 Chat ID |
| `TG_CHANNEL` | 频道用户名 (可选，如 `@channel_name`) |
| `CREDITS_TOTAL` | 总Credits额度 (面板显示用) |
| `CREDITS_DAILY` | 每日Credits消耗 (面板显示用) |

## 推荐部署

在服务器上用 `--bot` 模式长期运行，配合 `systemd` 或 `screen`/`tmux`：

```bash
export TG_TOKEN="your_bot_token"
export TG_CHAT_ID="your_chat_id"
nohup python3 monitor.py --bot > monitor.log 2>&1 &
```
