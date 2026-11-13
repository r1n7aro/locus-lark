"""Runs the fake Feishu service until it is stopped, for trying the plugin in
a Locus test instance: start Locus with ``LOCUS_LARK_TEST_ENDPOINT`` set to
the printed URL. Never use it with a real account."""

from __future__ import annotations

import threading

from fake_lark import FakeLark


def main() -> None:
    fake = FakeLark()

    @fake.route("GET", "/open-apis/im/v1/chats")
    def chats(request):
        return {"items": [{"chat_id": "oc_e2e", "name": "联调群"}], "has_more": False}

    @fake.route("POST", "/open-apis/im/v1/messages")
    def send(request):
        body = request.json
        return {"message_id": "om_e2e", "chat_id": body["receive_id"], "msg_type": body["msg_type"],
                "body": {"content": body["content"]}}

    print(f"FAKE_URL {fake.url}", flush=True)
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        pass
    finally:
        fake.close()


if __name__ == "__main__":
    main()
