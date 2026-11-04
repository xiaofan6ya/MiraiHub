"""CodeArts 上游端点表（一个地方改地址，全平台生效）。

## 地址来源

`<安装目录>/resources/product.json`（本机实测在 D 盘，代码里不写盘符）：

```json
"snapEngineUrl": "https://snap-access.cn-north-4.myhuaweicloud.com",
"auth": {"iamStsHost": "https://sts.cn-north-4.myhuaweicloud.com", ...},
"kernel": {"envConfig": {"inferhubBaseUrl": ".../api/v2/"}}
```

## 三条链路的鉴权是**同一套**（AK/SK 签名 + X-Security-Token）

| 用途 | 路径 | 备注 |
|---|---|---|
| 对话 | `/api/v2/chat/completions` | 兼容 OpenAI，`stream: true` 是真 SSE |
| 模型目录 | `/v1/model/builtin` | **必须带 `Agent-Type: PromptCenter`**，否则 400 |
| 额度 | `/snap-manager/v1/statistics/plugin` | 返回各积分包的 amount/used/remain |
| 身份 | `/v5/caller-identity` | 探活/校验凭证（签名同上） |

`/api/v2/models` 看起来更像，但实测 **404**（APIG.0101 未发布）——
"看起来对称的路径"是这类拆包最常踩的坑，这里把实测结论写在表里。
"""
from __future__ import annotations

#: 平台落库 id（对外平台 id 也是它）
PLATFORM = "codearts"

#: 区域（product.json 的 auth.region）
REGION = "cn-north-4"

#: 业务主机：对话 / 模型 / 额度 / 身份都在这一个域下
BASE = "https://snap-access.cn-north-4.myhuaweicloud.com"

CHAT = "/api/v2/chat/completions"
MODELS = "/v1/model/builtin"
QUOTA = "/snap-manager/v1/statistics/plugin"
IDENTITY = "/v5/caller-identity"

#: 模型目录接口要求的 Agent-Type（缺了直接 400「请求头Agent-Type为空」）
AGENT_TYPE = "PromptCenter"

#: 语言头（影响错误文案，不影响协议）
LANGUAGE = "zh-cn"


def url(path: str) -> str:
    """拼绝对地址；传绝对 URL 时原样返回（便于测试替换域名）。"""
    if path.startswith("http://") or path.startswith("https://"):
        return path
    return BASE + path
