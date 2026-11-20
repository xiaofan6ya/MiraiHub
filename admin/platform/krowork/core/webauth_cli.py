"""KroWork 手机号授权登录 —— 命令行版（不依赖客户端已登录）。

用法：

    # 交互式：一步一步来
    python -m admin.platform.krowork.core.webauth_cli

    # 一条命令跑完（验证码提前准备好）
    python -m admin.platform.krowork.core.webauth_cli --phone 13800138000 --code 123456

    # 只拿授权链接（自己丢浏览器里登，登完把回调 URL 贴回来）
    python -m admin.platform.krowork.core.webauth_cli --url-only
    python -m admin.platform.krowork.core.webauth_cli --redeem "http://127.0.0.1:17321/callback?code=...&state=..." --verifier <verifier>

成功后会：
  1. 把 id_token 写进 `%LOCALAPPDATA%\\krow\\id_token.txt`（网关下次自动读）
  2. 顺手入号池（有数据库时）
"""
from __future__ import annotations

import argparse
import json
import sys


def _print(obj) -> None:
    print(json.dumps(obj, ensure_ascii=False, indent=2))


def main(argv: list[str] | None = None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001
        pass

    parser = argparse.ArgumentParser(description="KroWork 手机号授权登录")
    parser.add_argument("--phone", default="", help="手机号（11 位，不带国家码）")
    parser.add_argument("--country", default="+86", help="国家码，默认 +86")
    parser.add_argument("--code", default="", help="短信验证码（不给就交互输入）")
    parser.add_argument("--realm", default="cn", choices=("cn", "global"))
    parser.add_argument("--url-only", action="store_true",
                        help="只输出授权链接与 verifier，不登录")
    parser.add_argument("--redeem", default="", help="手动兑换：回调 URL")
    parser.add_argument("--verifier", default="", help="手动兑换时的 code_verifier")
    parser.add_argument("--no-db", action="store_true", help="只写 token 文件，不入号池")
    args = parser.parse_args(argv)

    from admin.platform.krowork.core import token as token_mod
    from admin.platform.krowork.core import webauth

    # ---- 手动兑换 ------------------------------------------------------
    if args.redeem:
        result = webauth.redeem(args.redeem, args.verifier, args.realm)
        _print(result)
        if not result.get("ok"):
            return 1
        _persist(result["id_token"], token_mod, args.no_db,
                 result.get("refresh_token", ""), args.realm)
        return 0

    # ---- 建会话 --------------------------------------------------------
    session = webauth.begin(args.realm)
    if session.get("status") == "failed":
        _print(session)
        return 1

    if args.url_only:
        full = webauth.get_session(session["auth_id"])
        _print({
            "auth_url": session["auth_url"],
            "verifier": full.verifier if full else "",
            "redirect_uri": webauth.REDIRECT_URI,
            "提示": ("浏览器打开 auth_url 完成登录；跳转后地址栏会停在 "
                     "127.0.0.1:17321/callback?code=... —— 把整条 URL 和 verifier "
                     "一起交给 --redeem"),
        })
        return 0

    phone = args.phone or input("手机号（11 位）：").strip()
    sent = webauth.send_code(session["auth_id"], phone, args.country)
    print("发码：", sent.get("detail"))
    if not sent.get("ok"):
        return 1

    code = args.code or input("短信验证码：").strip()
    result = webauth.submit(session["auth_id"], code, phone, args.country)
    _print(result)
    if not result.get("ok"):
        return 1
    # submit 内部已经入池，这里只补落盘（submit 入库时可能没有 DB）
    return 0


def _persist(id_token: str, token_mod, no_db: bool, refresh: str,
             realm: str) -> None:
    path = token_mod.data_dir() / "id_token.txt"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(id_token, encoding="utf-8")
    token_mod.STORE.invalidate()
    print("已写入:", path)
    if no_db:
        return
    try:
        from admin.db import SessionLocal
        from admin.platform.krowork.services import accounts as ka

        with SessionLocal() as db:
            account, created = ka._upsert(db, {
                "id_token": id_token,
                "refresh_token": refresh,
                "expires_at": int(token_mod.token_exp(id_token) or 0),
                "source": "redeem",
                "region": realm,
            })
            db.commit()
            print("已入号池: id=%s created=%s" % (account.id, created))
    except Exception as exc:  # noqa: BLE001
        print("入号池失败（token 文件已写好，不影响使用）：%s" % exc)


if __name__ == "__main__":
    raise SystemExit(main())
