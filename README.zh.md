# 飞书 / Lark for Locus

这个插件把 Locus 连接到飞书 / Lark。在插件的设置页登录一次后，Locus python 工具里的脚本就能通过 `locus.lark` 读写飞书内容，身份可以是你本人，也可以是应用的机器人。

```python
lark = locus.lark
chat = await lark.chats.find("项目群")
async for message in chat.messages(since="2026-11-01"):
    print(message.sender.name, message.text)
doc = await lark.open("https://example.feishu.cn/wiki/...")
await doc.append("## 结论\n- ...")
```

## 覆盖范围

| 领域 | 能力 |
|---|---|
| 知识库 | 空间；页面树；创建、重命名、移动、复制页面，创建快捷方式；空间成员 |
| 云文档 | 以 Markdown 或 DocxXML 读写；块级编辑；插入图片和附件；历史版本与恢复；搜索 |
| 云空间 | 文件夹；上传、下载；导入（Word、Markdown、Excel、CSV）；导出（docx、pdf、md、xlsx、csv）；复制、移动、删除 |
| 评论与分享 | 评论和回复、协作者、转移所有者、链接分享；文档、表格、多维表格和文件都支持 |
| 电子表格 | 读取区域、按记录读取、写入、追加、清空、查找替换、增删行列、合并单元格、工作表管理 |
| 多维表格 | 数据表、字段、视图；记录可按条件筛选和排序查询，可批量增改删 |
| 群聊与消息 | 读历史、搜索；发送和回复文本、Markdown、图片、文件、卡片；撤回、编辑、转发、合并转发；表情回复、Pin、群置顶、加急、群公告；成员与管理员；建群、解散群 |
| 其他接口 | `lark.api` 用同一登录调用任意开放平台接口 |

## 使用

1. 安装插件，打开「插件 → 飞书 / Lark → 设置」。
2. 设置应用，二选一：
   - 点「一键创建应用」，在飞书中确认后，会在你的租户里创建一个个人应用；
   - 填写已有应用的 App ID 和 App Secret。
3. 点「登录」，在飞书中确认。

以你的身份发消息、管理群公告等可选权限需要单独申请。部分企业中，这类权限要由管理员审批。

## 实现方式

- **登录**：由 `lark_auth` 工具负责。app secret 和 refresh token 只有这个工具持有，都保存在系统钥匙串中。脚本只拿到短期 access token，并通过一个长连接直接访问开放平台。
- **无常驻开销**：安装后不增加 Rule，也不增加常驻工具。
  - 唯一的工具按需加载。
  - Skill 在上下文中只占一行摘要。
  - `locus.lark` 及其子模块在首次使用时才导入。`httpx` 第一次用到时才安装到所选 Python 中。
- **版本要求**：Locus 0.9.4 或更高。

## 许可

MIT。登录协议参照官方飞书 CLI（[larksuite/cli](https://github.com/larksuite/cli)，MIT）和 Python SDK（[lark-oapi](https://github.com/larksuite/oapi-sdk-python)，MIT），详见 [LICENSE](LICENSE)。
