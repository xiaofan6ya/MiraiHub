# 4100404 误判复盘：把「账号被封」当成「签名不对」

> 时间：2026-11-19 ~ 11-20
> 影响：一整天排查方向错误，多装了 4.9 GB 的 WSL + wine，写了 20 多个
> 一次性诊断脚本，合入了一份错误的短包模板。
> 结论：**本地 DLL 从 PR #33 接入那天起就是好的。**

---

## 一句话结论

`4100404 请求被风控拦截` 有两种完全不同的成因，**必须先分清是哪一种**：

| 成因 | 特征 | 处置 |
|---|---|---|
| **账号 / 出口被封** | 所有账号同时 403，连空 body 也 403 | 等窗口过去，或换号换出口 |
| **签名自证不对** | 换号有效、时间过去无效 | 修 uskey / conv / rid |

2026-11-19 那次是**第一种**，但被诊断成了第二种。

---

## 时间线（都能在 `logs/admin.log` 与 `request_audits` 里查到）

```
11-19 05:00  ok=275  429=0     正常
11-19 06:00  ok=304  429=0     正常
11-19 07:00  ok=197  429=8     开始出现 4100404
11-19 08:00  ok=0    429=1     彻底不通
11-19 10:35  ——      最后一次 429
11-19 11:00  ok=62   429=0     自己恢复了
11-20 07:53  最后一次 200（持续正常）
```

关键事实：**没有任何一次「改代码」的动作，是它自己恢复的。**

而当时的账号表：

```
id=402  status=disabled  cool_kind=manual_disabled  err_count=71
id=403  status=disabled  cool_kind=manual_disabled  err_count=60
id=404  status=disabled  cool_kind=manual_disabled  err_count=35
id=411  status=disabled  cool_kind=manual_disabled  err_count=72
id=414  status=active    cool_kind=risk_control     err_count=1
```

5 个号里 4 个被人工停用，只剩 1 个还在撞墙。**这个状态摆在眼前，却被当成了
「403 导致的结果」，而不是「403 的原因」。** 因果搞反了。

---

## 两个具体的误判

### 误判一：把「264 字节」当成上游认的唯一形态

观察到的事实：

```
线上 wine 签出的    352 字符 / 264 字节  →  200
本地 DLL 签出的     504 字符 / 376 字节  →  403
```

于是结论成了「264 才是上游认的形态」，还专门实现了 208 字节 TLV 的短版模板
（`uskey_local.build_tlv_short`，magic 改成 `0ac0d50c2111`）。

**实测推翻：**

```
本地 DLL 签 504 字符 / 376 字节  →  连打 5 次全 200
```

长度根本不是判据。当时的对照组是无效的：线上那枚好运的时候，账号恰好出了封禁窗口。

### 误判二：把「线上能过」当成「线上签名更对」

线上那台跑的是 `_beacon.sign()`（wine + 真 DLL），本地跑的是纯 Python
`mint_uskey`。两者确实不同，但**差异被错误归因了**：

```
纯 Python mint 的 magic = 030057040111
真 DLL     的 magic = 0ac0d50c2111
```

这个差异是真的（纯 Python 复刻确实和 DLL 不是一套），但它**不是 403 的原因**——
因为纯 Python 那次失败时，账号也在封禁窗口里。

---

## 现在的最简可用形态

```
网关 (127.0.0.1:8790)
  └─ uskey_signer.sign_for()
       └─ vendor/beacon/beacon_wrapper.dll   ← 3.5 MB，Windows 直接加载
            + qimei.dll                        ← 0.9 MB
```

配置只要一行：

```env
MARVIS_USKEY_BACKEND=dll
```

**不需要** WSL、不需要 wine、不需要任何签名服务、不依赖线上。

已验证：连打 5 次全 200。

---

## 下次再遇到 4100404，按这个顺序查

### 第 1 步：确认是不是账号 / 出口被封（30 秒）

```sql
SELECT id, name, status, cool_kind, err_count, last_err_msg
FROM accounts WHERE platform='marvis' ORDER BY id;
```

判据：

- **多个号同时 403** → 账号或出口被封，**别再动签名代码**
- 只有单个号 403、换号就好 → 才可能是账号问题
- `cool_kind = manual_disabled` → 人手停的，不是程序判的

### 第 2 步：确认是不是签名问题（1 分钟）

```bash
python _scratch/check_current_path.py
```

它会用当前配置签一枚、打一次上游，并打印实际走的后端与签出长度。

- 签出成功但 403，且**所有号都 403** → 账号层面的事
- 签出报 `signer_unavailable` → 才是签名器的事

### 第 3 步：不要做的事

| 别做 | 为什么 |
|---|---|
| 看到 403 就改 uskey 长度 / magic | 长度不是判据（本地 376 字节也能 200） |
| 看到线上能过就认为线上签名更好 | 线上和本地用的是同一份 DLL |
| 为签名装虚拟机 / wine | 除非 `signer_unavailable`，否则用不上 |
| 拿「某次能过 / 某次不能过」当对照 | 封禁窗口会自己开合，单次对照没有意义 |

---

## 保留下来的东西（以及为什么）

这次排查虽然方向错了，但有几样东西是真的：

| 保留 | 理由 |
|---|---|
| `uskey_signer.sign_for()` 的三档后端 | 真遇到签名器坏掉时能快速切；默认 `dll` |
| `device.py` 的 qimei 格式（`24hex + 3000 + 8hex`） | 真机就是这个格式，之前生成的完全不合法 |
| `request_payload` 开关实时读 settings | 真实 bug，与本次误判无关 |
| PR #67 的风控退避 | 真实问题：一次 4100404 不该冻结整池 |

## 已经删掉的

| 删掉 | 理由 |
|---|---|
| WSL2 + wine + 签名服务 | 4.9 GB 磁盘，零收益 |
| `build_tlv_short()` 短版模板 | 基于错误结论（长度是判据） |
| 各类 `tunnel_sign` / `uskey_proxy` / `qimei_downgrade` | 都是为错误结论服务的一次性工具 |

> 短版模板代码本身还在 `uskey_local.py` 里（`short=` 参数），但**默认关闭**，
> 且注释里写清了「已验证对上游无效，别当修复用」。
